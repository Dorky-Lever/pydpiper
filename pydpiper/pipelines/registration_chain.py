#!/usr/bin/env python

from   __future__ import print_function
import csv
from collections import defaultdict

from atom.api import Atom, Int, Str, Dict, Enum, Instance
#import atom.api as atom

from pydpiper.minc.analysis import determinants_at_fwhms
from pydpiper.minc.registration import Stages, mincANTS_NLIN_build_model, mincANTS_default_conf, MincANTSConf, mincANTS, intrasubject_registrations
from pydpiper.minc.files import MincAtom
#from pydpiper.pipelines.LSQ6 import lsq6

# TODO: this might be temporary... currently only used 
# to test the registration chain
import sys
# TODO: same thing here..
import os

# TODO (general for all option records, not just for the registration chain):
# namedtuples are better than Argparse Namespaces for specification 
# (more descriptive name -- other benefits?) and
# for being able to get the data back out again (used in PP for filtering & re-sending
# arguments via submit scripts, which is the wrong approach, but ...),
# but they don't have the built-in type/arity checking or monoid-ish operations
# of Argparse, so we should create our own type which can be sent to both ??

# ALSO it's unclear whether our attempt at 'nominal' typing is a good idea, since, e.g.,
# a chain calls LSQ6, so will have to have lsq6 options as a subset of its own options
# ... so we could have a nested record, but this might not be best if both the chain
# and lsq6 need a particular option ... (also executor options, ...) ??

class ChainConf(Atom):
    input_space            = Enum('native', 'lsq6', 'lsq12')
    common_time_point      = Instance(type(None),int)
    common_time_point_name = Str("common")
    csv_file               = Instance(type(None), str)
    # perhaps the following belongs in a different class...
    stats_kernels          = Str("0.5,0.2,0.1") 

class Subject(Atom):
    intersubject_registration_time_pt = Instance(int)
    time_pt_dict   = Dict()    # validation (key=Int, value=Str) doesn't work? ...

    def __eq__(self, other):
        return (self is other or
                (self.intersubject_registration_time_pt == other.intersubject_registration_time_pt
                 and self.time_pt_dict == other.time_pt_dict
                 and self.__class__ == other.__class__))
    # ugh; also, should this be type(self) == ... ?

    def get_intersubject_registration_image(self):
        return self.time_pt_dict[self.intersubject_registration_time_pt]

    intersubject_registration_image = property(get_intersubject_registration_image,
                                               'intersubject_registration_image property')
    
# could be a method/property; unsure how well this works with Traits/Atom
def intersubject_registration_image(subject):
    return subject.time_pt_dict[subject.intersubjection_registration_time_pt]

class TimePointError(Exception):
    pass
                 
def map_data(f, d): # TODO find a new name for this
    # TODO this is probably too big to be a doctest ...
    """Map `f` non-destructively (if `f` is) over (the values of)
    the inner time_pt_dict of a { subject : Subject }
    
    >>> (map_data(lambda x: x[3],
    ...          { 's1' : Subject(intersubject_registration_time_pt=4, time_pt_dict={3:'s1_3.mnc', 4:'s1_4.mnc'}),
    ...            's2' : Subject(intersubject_registration_time_pt=4, time_pt_dict={4:'s2_4.mnc', 5:'s2_5.mnc'})} )
    ...   == { 's1' : Subject(intersubject_registration_time_pt=4, time_pt_dict= {3:'3',4:'4'}),
    ...        's2' : Subject(intersubject_registration_time_pt=4, time_pt_dict= {4:'4',5:'5'}) })
    True
    """
    new_d = {}
    for s_id, subj in d.iteritems():
        new_time_pt_dict = {}
        for t,x in subj.time_pt_dict.iteritems():
            new_time_pt_dict[t] = f(x)
        new_subj = Subject(intersubject_registration_time_pt = subj.intersubject_registration_time_pt,
                           time_pt_dict   = new_time_pt_dict)
        new_d[s_id] = new_subj
    return new_d

def parse_common(string):
    truthy_strings = ['1','True','true','T','t']
    falsy_strings  = ['','0','False','false','F','f']
    def fmt(strs):
        return "'" + "','".join(strs) + "'"
    string = string.strip()
    if string in truthy_strings:
        return True
    elif string in falsy_strings:
        return False
    else:
        raise ValueError('Unrecognized value %s; ' % string
                         + 'Please use one of ' + fmt(truthy_strings)
                         + ' in the "is_common" field of your csv file ' 
                         + 'to use this file for intersubject registration, or '
                         + 'one of ' + fmt(falsy_strings) + 'to specify otherwise')

# TODO standardize on pt/point
# TODO write some longer (non-doc)tests
def parse_csv(rows, common_time_pt): # row iterator, int -> { subject_id(str) : Subject }
    """
    Read subject information from a csv file containing at least the columns
    'subject_id', 'timepoint', and 'filename', and optionally a 'bitfield' column
    'is_common' containing one 1 per subject and 0 or empty fields for the other scans.
    Return a map from subject IDs to a dict of timepoints and a specific timepoint
    to be used for inter-subject registration.

    >>> csv_data = "subject_id,timepoint,filename,genotype\\ns1,1,s1_1.mnc,1\\n".split('\\n')
    >>> (parse_csv(csv_data, common_time_pt=1)
    ...   == { 's1' : Subject(intersubject_registration_time_pt=1, time_pt_dict={ 1 : 's1_1.mnc' })})
    True
    """
    subject_info = defaultdict(Subject)
    # Populate the subject -> Subject dictionary from the rows"""
    for row in csv.DictReader(rows):
        try:
            subj_id   = row['subject_id']
            timepoint = int(row['timepoint'])
            filename  = row['filename']
        except KeyError as e:
            raise KeyError("csv file must contain at least "
                           "'subject_id', 'timepoint', 'filename' fields; "
                           "missing: %s" % e.message)
        else:
            subject_info[subj_id].time_pt_dict[timepoint] = MincAtom(name=filename,
                                                                     orig_name=filename)
            if parse_common(row.get('is_common', '')):
                if subject_info[subj_id].intersubject_registration_time_pt is not None:
                    raise TimePointError(
                        "duplicate common time point specified for subject '%s'"
                        % subj_id)
                else:
                    subject_info[subj_id].intersubject_registration_time_pt = timepoint
    # could make this part into a separate fn that copies input, returns updated version:
    # Iterate through subjects, filling in intersubject-registration time points with the common
    # time point if unspecified for a given subject, and raising an error if there's no timepoint
    # available or no scan for the specified timepoint
    for s_id, s in subject_info.iteritems():
        if s.intersubject_registration_time_pt is None:
            if common_time_pt is None:
                raise TimePointError("no subject-specific or default inter-subject "
                                     "time point provided for subject '%s'" % s_id)
            elif common_time_pt in s.time_pt_dict.keys():
                s.intersubject_registration_time_pt = common_time_pt
            elif common_time_pt == -1 or common_time_pt == "-1":
                s.intersubject_registration_time_pt = max(s.time_pt_dict.keys())
            else:
                raise TimePointError("subject '%s' didn't have a scan for "
                                     "the common time point specified (%s); "
                                     "fix this or specify a different timepoint "
                                     "for this subject by putting a value in an "
                                     "'is_common' column of your table"
                                     % (s_id, str(common_time_pt)))
        else:
            if common_time_pt != s.intersubject_registration_time_pt:
                print('note: overriding common_time_pt %d with time point %d for subject %s'
                      % (common_time_pt, s.intersubject_registration_time_pt, s_id))
                    
    return subject_info
    
    #print(timepts)
    
    #timepts = subject_info[subj]
    #print(timepts)
    #raise NotImplementedError()


# NOTE I've moved the optional lsq6 stuff outside this function to promote re-use;
# actual call could look something like this:
#def chain_with_optional_lsq6(inputs, options):
#    def native():
#        pass
#    def lsq6():
#        pass
#
#    fns = { 'native' : native, 'lsq6' : lsq6 }
#
#    try:
#        f = fns[options.input_space]
#    except KeyError:
#        raise ValueError("illegal input space: %s; allowed options: %s" % \
#                         (options.input_space, ','.join(map(str,fns.keys()))))
#    # call f...


def chain(options):

    s = Stages()
    
    with open(options.csv_file, 'r') as f:
        subject_info = parse_csv(f, options.common_time_point)
    
    if options.input_space not in ChainConf.input_space.items:
        raise ValueError('unrecognized input space: %s; choices: %s' % (options.input_space, ChainConf.input_space.items))
    
    if options.input_space == 'native':
        raise NotImplementedError("We currently have not implemented the code for 'input space': %s" % options.input_space)
    

    
    
    #all_imgs = {(s_id,t):img for s_id, subj in subject_info.iteritems()
    #            for (t,img) in subj.time_point_dict.iteritems()}

    # NB currently LSQ6 expects an array of files, but we have a map.
    # possibilities:
    # - note that pairwise is enough (except for efficiency -- redundant blurring, etc.)
    #   and just use the map fn above with an LSQ6 fn taking only a single source
    # - rewrite LSQ6 to use such a (nested) map
    # - write conversion which creates a tagged array from the map, performs LSQ6,
    #   and converts back
    # - write 'over' which takes a registration, a data structure, and 'get/set' fns ...?

    #all_imgs = [img for subj in subject_info.itervalues()
    #            for img in subj.time_point_dict.itervalues()]

    # TODO how to associate images in the above dict with their xfm ??
    # put result of LSQ6 into a map img_name => xfm
    #lsq6_xfms = s.defer(LSQ6(all_imgs, options.lsq6_conf))
    
    #{ xfm.source : xfm for xfm in lsq6_xfms}

    # Intersubject registration: LSQ12/NLIN registration of common-timepoint images
    # The assumption here is that all these files are roughly aligned. Here is a toy
    # schematic of what happens. In this example, the common timepoint is set timepoint 2: 
    #
    #                            ------------
    # subject A    A_time_1   -> | A_time_2 | ->   A_time_3
    # subject B    B_time_1   -> | B_time_2 | ->   B_time_3
    # subject C    C_time_1   -> | C_time_2 | ->   C_time_3
    #                            ------------
    #                                 |
    #                            group_wise registration on time point 2
    #
    intersubj_imgs = { s_id : subj.intersubject_registration_image
                       for s_id, subj in subject_info.iteritems() }
    print("\nImages that are used for the intersubject registration:")
    for subject in intersubj_imgs:
        print("ID:   ", subject, "\nFile: ", intersubj_imgs[subject].orig_path) 
    if options.input_space == 'lsq6':
        raise NotImplementedError("We currently have not implemented the code for 'input space': %s" % options.input_space)
        #intersubj_xfms = lsq12_NLIN_build_model_on_dictionaries(imgs=intersubj_imgs,
        #                                                        conf=conf,
        #                                                        lsq12_dir=lsq12_directory
                                                                #, like={atlas_from_init_model_at_this_tp}
        #                                                        )
    elif options.input_space == 'lsq12':
        some_temp_dir =  os.getcwd()  + "/nlin_dir_testing/"
        test_conf = mincANTS_default_conf
        intersubj_xfms = s.defer(mincANTS_NLIN_build_model(imgs=intersubj_imgs.values(),
                                                   initial_target=intersubj_imgs.values()[0], # this doesn't make sense yet
                                                   nlin_dir=some_temp_dir,
                                                   confs=[test_conf]))
    print("\n*** *** INTERSUBJECT STAGES *** ***\n")
    for stage in s:
        print(stage.to_string(),"\n")
    
    ## within-subject registration
    # In the toy scenario below: 
    # subject A    A_time_1   ->   A_time_2   ->   A_time_3
    # subject B    B_time_1   ->   B_time_2   ->   B_time_3
    # subject C    C_time_1   ->   C_time_2   ->   C_time_3
    # 
    # The following registrations are run:
    # 1) A_time_1   ->   A_time_2
    # 2) A_time_2   ->   A_time_3
    #
    # 3) B_time_1   ->   B_time_2
    # 4) B_time_2   ->   B_time_3
    #
    # 5) C_time_1   ->   C_time_2
    # 6) C_time_2   ->   C_time_3    
    chain_xfms = { s_id : s.defer(intrasubject_registrations(subj))
                   for s_id, subj in subject_info.iteritems() }
    
    print("\n*** *** INTRASUBJECT STAGES *** ***\n")
    for stage in s:
        print(stage.to_string(),"\n")
    #for subject_cmd_stage in chain_xfms:
    #    for cmd_stage in chain_xfms[subject_cmd_stage].stages:
    #        print(cmd_stage.to_string(), "\n")

    # TODO n

    ## longitudinal registration
    #for subj_id, subj in subject_info.iteritems():
    #    pass

    # TODO temp, just to see if we now the transformation information we need
    
    print(intersubj_xfms)
    print(chain_xfms)
    
    # this contains the information for all subjects
    for s_id, subj in subject_info.iteritems():
        print("Intersubj time point: %s" % subj.intersubject_registration_time_pt)
        for subj_time_pt, subj_time_pt_file in subj.time_pt_dict.iteritems():
            print("(File, timepoint): %s, %s" % (subj_time_pt_file.orig_path, subj_time_pt))
            if subj_time_pt_file.orig_path == subj.intersubject_registration_image.orig_path:
                # this file has the common time point transformation
                for xfmhandler in intersubj_xfms.xfms:
                    if xfmhandler.source == subj_time_pt_file:
                        print(xfmhandler.xfm.get_path())
        
    map_data(lambda xfm: determinants_at_fwhms(xfm, options.stats_kernels), subject_info)
    
    #raise NotImplemented



if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Error: running in testing mode. Provide the following: \n \n" \
            "filename_spread.csv \n" \
            "input_space (possibilities: native, lsq6, lsq12) \n" \
            "string_with_blurs_for_stat_files (e.g. 0.5,0.2,0.1) \n" \
            "(optionally) common_time_point \n")
        sys.exit(1)
    
    options = ChainConf()
    options.csv_file = sys.argv[1]
    options.input_space = sys.argv[2]
    options.stats_kernels = sys.argv[3]
    if len(sys.argv) == 5:
        options.common_time_point = int(sys.argv[4])
    else:
        options.common_time_point = -1
    
    chain(options)
    
    print("\nDone...\n")
    
    #print "Number of arguments: ", len(sys.argv)
    #print "Argument list: ", str(sys.argv)
    
