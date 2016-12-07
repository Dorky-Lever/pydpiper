#!/usr/bin/env python3

import os.path
import warnings

import numpy as np
import pandas as pd
import sys
from configargparse import Namespace, ArgParser
from typing import List

from pydpiper.minc.containers import XfmHandler

from pydpiper.core.files import FileAtom
from pydpiper.minc.thickness import cortical_thickness
from pydpiper.pipelines.MAGeT import maget, maget_parser, maget_parsers, fixup_maget_options, maget_mask
from pydpiper.core.util import NamedTuple, maybe_deref_path
from pydpiper.core.stages       import Result, Stages

#TODO fix up imports, naming, stuff in registration vs. pipelines, ...
from pydpiper.minc.files        import MincAtom, XfmAtom
from pydpiper.minc.registration import (lsq6_nuc_inorm, lsq12_nlin_build_model, registration_targets,
                                        LSQ6Conf, LSQ12Conf, get_resolution_from_file, concat_xfmhandlers,
                                        get_nonlinear_configuration_from_options,
                                        invert_xfmhandler, check_MINC_input_files, lsq12_nlin, MultilevelMincANTSConf,
                                        LinearTransType, get_linear_configuration_from_options, mincresample_new,
                                        Interpolation, param2xfm)
from pydpiper.minc.analysis     import determinants_at_fwhms, StatsConf
from pydpiper.minc.thickness    import thickness_parser
from pydpiper.core.arguments    import (lsq6_parser, lsq12_parser, nlin_parser, stats_parser, CompoundParser,
                                        AnnotatedParser, NLINConf, BaseParser, segmentation_parser)
from pydpiper.execution.application    import mk_application


MBMConf = NamedTuple('MBMConf', [('lsq6',  LSQ6Conf),
                                 ('lsq12', LSQ12Conf),
                                 ('nlin',  NLINConf),
                                 ('stats', StatsConf)])


def mbm_pipeline(options : MBMConf):
    s = Stages()

    if options.application.csv_file and options.application.files:
        sys.exit("both --csv-file and --files specified ...")

    if options.application.csv_file:
        try:
            csv = pd.read_csv(options.application.csv_file)
        except:
            warnings.warn("couldn't read csv ... did you supply `file` column?")
            raise

        # TODO extract into a function for, e.g., twolevel
        if hasattr(csv, 'mask_file'):
            masks = [MincAtom(mask, pipeline_sub_dir=os.path.join(options.application.output_directory,
                                                                  options.application.pipeline_name + "_processed"))
                     if not np.isnan(mask) else None
                     for mask in csv.mask_file]
        else:
            masks = [None] * len(csv.files)

        imgs = [MincAtom(name, mask=mask,
                         pipeline_sub_dir=os.path.join(options.application.output_directory,
                                                       options.application.pipeline_name + "_processed"))
                # TODO does anything break if we make imgs a pd.Series?
                for name, mask in zip(csv.files, masks)]
    elif options.application.files:
        imgs = [MincAtom(name, pipeline_sub_dir=os.path.join(options.application.output_directory,
                                                             options.application.pipeline_name + "_processed"))
                for name in options.application.files]

    check_MINC_input_files([img.path for img in imgs])

    mbm_result = s.defer(mbm(imgs=imgs, options=options,
                             prefix=options.application.pipeline_name,
                             output_dir=options.application.output_directory))

    # create useful CSVs (note the files listed therein won't yet exist ...)
    for filename, dataframe in (("transforms.csv", mbm_result.xfms),
                                ("determinants.csv", mbm_result.determinants)):
        with open(filename, 'w') as f:
            f.write(dataframe.applymap(maybe_deref_path).to_csv(index=False))

    # # TODO moved here from inside `mbm` for now ... does this make most sense?
    # if options.mbm.segmentation.run_maget:
    #     import copy
    #     maget_options = copy.deepcopy(options)  #Namespace(maget=options)
    #     #maget_options
    #     #maget_options.maget = maget_options.mbm
    #     #maget_options.execution = options.execution
    #     #maget_options.application = options.application
    #     maget_options.application.output_directory = os.path.join(options.application.output_directory, "segmentation")
    #     maget_options.maget = options.mbm.maget
    #
    #     fixup_maget_options(maget_options=maget_options.maget,
    #                         nlin_options=maget_options.mbm.nlin,
    #                         lsq12_options=maget_options.mbm.lsq12)
    #     del maget_options.mbm
    #
    #
    #     #def with_new_output_dir(img : MincAtom):
    #         #img = copy.copy(img)
    #         #img.pipeline_sub_dir = img.pipeline_sub_dir + img.output_dir
    #         #img.
    #         #return img.newname_with_suffix(suffix="", subdir="segmentation")
    #
    #     s.defer(maget([xfm.resampled for _ix, xfm in mbm_result.xfms.rigid_xfm.iteritems()],
    #                    options=maget_options,
    #                    prefix="%s_MAGeT" % prefix,
    #                    output_dir=os.path.join(options.application.output_directory, prefix + "_processed")))

    return Result(stages=s, output=mbm_result)


def mbm(imgs : List[MincAtom], options : MBMConf, prefix : str, output_dir : str = ""):

    # TODO could also allow pluggable pipeline parts e.g. LSQ6 could be substituted out for the modified LSQ6
    # for the kidney tips, etc...

    # TODO this is tedious and annoyingly similar to the registration chain ...
    lsq6_dir  = os.path.join(output_dir, prefix + "_lsq6")
    lsq12_dir = os.path.join(output_dir, prefix + "_lsq12")
    nlin_dir  = os.path.join(output_dir, prefix + "_nlin")

    s = Stages()

    if len(imgs) == 0:
        raise ValueError("Please, some files!")

    # FIXME: why do we have to call registration_targets *outside* of lsq6_nuc_inorm? is it just because of the extra
    # options required?  Also, shouldn't options.registration be a required input (as it contains `input_space`) ...?
    targets = registration_targets(lsq6_conf=options.mbm.lsq6,
                                   app_conf=options.application,
                                   first_input_file=imgs[0].path)

    # TODO this is quite tedious and duplicates stuff in the registration chain ...
    resolution = (options.registration.resolution or
                  get_resolution_from_file(targets.registration_standard.path))
    options.registration = options.registration.replace(resolution=resolution)


    # FIXME: this needs to go outside of the `mbm` function to avoid being run from within other pipelines (or
    # those other pipelines need to turn off this option)
    if options.mbm.segmentation.run_maget or options.mbm.maget.maget.mask:
        import copy
        maget_options = copy.deepcopy(options)  #Namespace(maget=options)
        #maget_options
        #maget_options.maget = maget_options.mbm
        #maget_options.execution = options.execution
        #maget_options.application = options.application
        #maget_options.application.output_directory = os.path.join(options.application.output_directory, "segmentation")
        maget_options.maget = options.mbm.maget

        fixup_maget_options(maget_options=maget_options.maget,
                            nlin_options=maget_options.mbm.nlin,
                            lsq12_options=maget_options.mbm.lsq12)
        del maget_options.mbm

        #def with_new_output_dir(img : MincAtom):
            #img = copy.copy(img)
            #img.pipeline_sub_dir = img.pipeline_sub_dir + img.output_dir
            #img.
            #return img.newname_with_suffix(suffix="", subdir="segmentation")

    # FIXME it probably makes most sense if the lsq6 module itself (even within lsq6_nuc_inorm) handles the run_lsq6
    # setting (via use of the identity transform) since then this doesn't have to be implemented for every pipeline
    if options.mbm.lsq6.run_lsq6:
        lsq6_result = s.defer(lsq6_nuc_inorm(imgs=imgs,
                                             resolution=resolution,
                                             registration_targets=targets,
                                             lsq6_dir=lsq6_dir,
                                             lsq6_options=options.mbm.lsq6))
    else:
        # FIXME the code shouldn't branch here based on run_lsq6 (which should probably
        # be part of the lsq6 options rather than the MBM ones; see comments on #287.
        # TODO don't actually do this resampling if not required (i.e., if the imgs already have the same grids)??
        # however, for now need to add the masks:
        identity_xfm = s.defer(param2xfm(out_xfm=FileAtom(name="identity.xfm")))
        lsq6_result  = [XfmHandler(source=img, target=img, xfm=identity_xfm,
                                   resampled=s.defer(mincresample_new(img=img,
                                                                      like=targets.registration_standard,
                                                                      xfm=identity_xfm)))
                        for img in imgs]
    # what about running nuc/inorm without a linear registration step??

    if options.mbm.maget.maget.mask:

        masking_imgs = copy.deepcopy([xfm.resampled for xfm in lsq6_result])
        masked_img = (s.defer(maget_mask(imgs=masking_imgs,
                                         resolution=resolution,
                                         maget_options=maget_options.maget,
                                         pipeline_sub_dir=os.path.join(options.application.output_directory,
                                                                       "%s_atlases" % prefix))))

        masked_img.index = masked_img.apply(lambda x: x.path)

        # replace any masks of the resampled images with the newly created masks:
        for xfm in lsq6_result:
            xfm.resampled = masked_img.ix[xfm.resampled.path]
    else:
        warnings.warn("Not masking your images from atlas masks after LSQ6 alignment ... probably not what you want "
                      "(this can have negative effects on your registration and statistics)")

    full_hierarchy = get_nonlinear_configuration_from_options(nlin_protocol=options.mbm.nlin.nlin_protocol,
                                                              reg_method=options.mbm.nlin.reg_method,
                                                              file_resolution=resolution)

    lsq12_nlin_result = s.defer(lsq12_nlin_build_model(imgs=[xfm.resampled for xfm in lsq6_result],
                                                       resolution=resolution,
                                                       lsq12_dir=lsq12_dir,
                                                       nlin_dir=nlin_dir,
                                                       nlin_prefix=prefix,
                                                       lsq12_conf=options.mbm.lsq12,
                                                       nlin_conf=full_hierarchy))

    inverted_xfms = [s.defer(invert_xfmhandler(xfm)) for xfm in lsq12_nlin_result.output]

    determinants = s.defer(determinants_at_fwhms(
                             xfms=inverted_xfms,
                             inv_xfms=lsq12_nlin_result.output,
                             blur_fwhms=options.mbm.stats.stats_kernels))

    overall_xfms = [s.defer(concat_xfmhandlers([rigid_xfm, lsq12_nlin_xfm]))
                    for rigid_xfm, lsq12_nlin_xfm in zip(lsq6_result, lsq12_nlin_result.output)]

    output_xfms = (pd.DataFrame({ "rigid_xfm"      : lsq6_result,  # maybe don't return this if LSQ6 not run??
                                  "lsq12_nlin_xfm" : lsq12_nlin_result.output,
                                  "overall_xfm"    : overall_xfms }))
    # we could `merge` the determinants with this table, but preserving information would cause lots of duplication
    # of the transforms (or storing determinants in more columns, but iterating over dynamically known columns
    # seems a bit odd ...)

                            # TODO transpose these fields?})
                            #avg_img=lsq12_nlin_result.avg_img,  # inconsistent w/ WithAvgImgs[...]-style outputs
                           # "determinants"    : determinants })

    #output.avg_img = lsq12_nlin_result.avg_img
    #output.determinants = determinants   # TODO temporary - remove once incorporated properly into `output` proper
    # TODO add more of lsq12_nlin_result?


    # FIXME moved above rest of registration for debugging ... shouldn't use and destructively modify lsq6_result!!!
    if options.mbm.segmentation.run_maget:
        maget_options = copy.deepcopy(maget_options)
        maget_options.maget.maget.mask = maget_options.maget.maget.mask_only = False   # already done above
        # use the original masks here otherwise the masking step will be re-run due to the previous masking run's
        # masks having been applied to the input images:
        s.defer(maget([xfm.resampled for xfm in lsq6_result],
                       #[xfm.resampled for _ix, xfm in mbm_result.xfms.rigid_xfm.iteritems()],
                       options=maget_options,
                       prefix="%s_MAGeT" % prefix,
                       output_dir=os.path.join(output_dir, prefix + "_processed")))
        # FIXME add pipeline dir to path and uncomment!
        #maget.to_csv(path_or_buf="segmentations.csv", columns=['img', 'voted_labels'])


    # TODO return some MAGeT stuff from MBM function ??
    # if options.mbm.mbm.run_maget:
    #     import copy
    #     maget_options = copy.deepcopy(options)  #Namespace(maget=options)
    #     #maget_options
    #     #maget_options.maget = maget_options.mbm
    #     #maget_options.execution = options.execution
    #     #maget_options.application = options.application
    #     maget_options.maget = options.mbm.maget
    #     del maget_options.mbm
    #
    #     s.defer(maget([xfm.resampled for xfm in lsq6_result],
    #                   options=maget_options,
    #                   prefix="%s_MAGeT" % prefix,
    #                   output_dir=os.path.join(output_dir, prefix + "_processed")))

    # should also move outside `mbm` function ...
    #if options.mbm.thickness.run_thickness:
    #    if not options.mbm.segmentation.run_maget:
    #        warnings.warn("MAGeT files (atlases, protocols) are needed to run thickness calculation.")
    #    # run MAGeT to segment the nlin average:
    #    import copy
    #    maget_options = copy.deepcopy(options)  #Namespace(maget=options)
    #    maget_options.maget = options.mbm.maget
    #    del maget_options.mbm
    #    segmented_avg = s.defer(maget(imgs=[lsq12_nlin_result.avg_img],
    #                                  options=maget_options,
    #                                  output_dir=os.path.join(options.application.output_directory,
    #                                                          prefix + "_processed"),
    #                                  prefix="%s_thickness_MAGeT" % prefix)).ix[0].img
    #    thickness = s.defer(cortical_thickness(xfms=pd.Series(inverted_xfms), atlas=segmented_avg,
    #                                           label_mapping=FileAtom(options.mbm.thickness.label_mapping),
    #                                           atlas_fwhm=0.56, thickness_fwhm=0.56))  # TODO magic fwhms
    #    # TODO write CSV -- should `cortical_thickness` do this/return a table?


    # FIXME: this needs to go outside of the `mbm` function to avoid being run from within other pipelines (or
    # those other pipelines need to turn off this option)
    if options.mbm.common_space.do_common_space_registration:
        warnings.warn("This feature is experimental ...")
        if not options.mbm.common_space.common_space_model:
            raise ValueError("No common space template provided!")
        # TODO allow lsq6 registration as well ...
        common_space_model = MincAtom(options.mbm.common_space.common_space_model,
                                      pipeline_sub_dir=os.path.join(options.application.output_directory,
                                                         options.application.pipeline_name + "_processed"))
        # TODO allow different lsq12/nlin config params than the ones used in MBM ...
        # WEIRD ... see comment in lsq12_nlin code ...
        nlin_conf  = full_hierarchy.confs[-1] if isinstance(full_hierarchy, MultilevelMincANTSConf) else full_hierarchy
        # also weird that we need to call get_linear_configuration_from_options here ... ?
        lsq12_conf = get_linear_configuration_from_options(conf=options.mbm.lsq12,
                                                           transform_type=LinearTransType.lsq12,
                                                           file_resolution=resolution)
        xfm_to_common = s.defer(lsq12_nlin(source=lsq12_nlin_result.avg_img, target=common_space_model,
                                           lsq12_conf=lsq12_conf, nlin_conf=nlin_conf,
                                           resample_source=True))

        model_common = s.defer(mincresample_new(img=lsq12_nlin_result.avg_img,
                                                xfm=xfm_to_common.xfm, like=common_space_model,
                                                postfix="_common"))

        overall_xfms_common = [s.defer(concat_xfmhandlers([rigid_xfm, nlin_xfm, xfm_to_common]))
                               for rigid_xfm, nlin_xfm in zip(lsq6_result, lsq12_nlin_result.output)]

        xfms_common = [s.defer(concat_xfmhandlers([nlin_xfm, xfm_to_common]))
                       for nlin_xfm in lsq12_nlin_result.output]

        output_xfms = output_xfms.assign(xfm_common=xfms_common, overall_xfm_common=overall_xfms_common)

        log_nlin_det_common, log_full_det_common = [dets.map(lambda d:
                                                      s.defer(mincresample_new(
                                                        img=d,
                                                        xfm=xfm_to_common.xfm,
                                                        like=common_space_model,
                                                        postfix="_common",
                                                        extra_flags=("-keep_real_range",),
                                                        interpolation=Interpolation.nearest_neighbour)))
                                                    for dets in (determinants.log_nlin_det, determinants.log_full_det)]

        determinants = determinants.assign(log_nlin_det_common=log_nlin_det_common,
                                           log_full_det_common=log_full_det_common)

    output = Namespace(avg_img=lsq12_nlin_result.avg_img, xfms=output_xfms, determinants=determinants)

    if options.mbm.common_space.do_common_space_registration:
        output.model_common = model_common

    return Result(stages=s, output=output)


# TODO move to arguments file?
def _mk_common_space_parser(parser : ArgParser):
    group = parser.add_argument_group("Common space options", "Options for registration/resampling to common (db) space.")
    group.add_argument("--common-space-model", dest="common_space_model",
                       type=str, help="Run MAGeT segmentation on the images.")
    group.add_argument("--no-common-space-registration", dest="do_common_space_registration",
                       default=False, action="store_false", help="Skip registration to common (db) space.")
    return parser

common_space_parser = AnnotatedParser(parser=BaseParser(_mk_common_space_parser(ArgParser(add_help=False)),
                                                        "common_space"),
                                      namespace="common_space")

mbm_parser = CompoundParser(
               [lsq6_parser,
                lsq12_parser,
                nlin_parser,
                stats_parser,
                common_space_parser,
                #thickness_parser,
                AnnotatedParser(parser=maget_parsers, namespace="maget", prefix="maget"),
                # TODO note that the maget-specific flags (--mask, --masking-method, etc., also get the "maget-" prefix)
                # which could be changed by putting in the maget-specific parser separately from its lsq12, nlin parsers
                segmentation_parser])

# TODO cast to MBMConf?
mbm_application = mk_application(parsers=[AnnotatedParser(parser=mbm_parser, namespace='mbm')],
                                 pipeline=mbm_pipeline)

if __name__ == "__main__":
    mbm_application()
