

import os
import warnings
from typing import Optional, Tuple

from pydpiper.core.files import FileAtom
from pydpiper.core.stages import CmdStage, Stages, Result
import pydpiper.itk.tools as itk
from pydpiper.itk.tools import ITKXfmAtom, convert, itk_convert_xfm
from pydpiper.minc.containers import XfmHandler, GenericXfmHandler
from pydpiper.minc.files import NiiAtom, XfmAtom, ToMinc
from pydpiper.minc.nlin import NLIN

# config_flags = [
#     (["-x"], 'ffd_x', {}),
#     (["-y"], 'ffd_y', {}),
#     (["-z"], 'ffd_z', {}),
#     (["-s"], 'scales', {}),
#     (["-o"], 'orientations', {}),
#     (["-c"], 'ms_weighting_mode', {}),
#     (["-w"], 'similarity_measure', { 'choices' : ["1", "2"] }),
#     (["-M"], 'force_histogram', {}),
#     (["-g"], 'regularization_weight', {}),
#     (["-n"], 'discrete_samples', {}),
#     (["-k"], 'max_iterations_each_res', {}),
#     # TODO lots more options here
#     (["-a"], 'affine', { 'choices' : ["0", "1", "2"] })
#     # TODO lots more options here
#     ]
#
#
# config_parser = argparse.ArgumentParser()
# for flags, dest, kwargs in config_flags:
#     config_parser.add_argument(*flags, dest=dest, **kwargs)
#
#
# file_specific_flags = [
#     #registration-specific options that don't really belong in a configuration file:
#     (["-S", "--source"], 'source', { 'required' : True }),
#     (["-T", "--target"], 'target', { 'required' : True }),
#     (["-O", "--outimg"], 'outimg', {}),
#     (["-D", "--outdef"], 'outdef', {}),
#     (["--bs"], "source_mask", {}),
#     (["--bt"], "target_mask", {}),
#     (["-d"], "initial_transform", {}),
#     ]
#
# flags = config_flags + file_specific_flags


# DRAMMSConf = NamedTuple(name="DRAMMSConf",
#                         fields=[#("ffd_spacing", Optional[Tuple[Optional[float], Optional[float], Optional[float]]]),
#                                 ("scales", Optional[int]),
#                                 ("orientations", Optional[int]),
#                                 ("saliency_usage", Optional[int]),
#                                 ("similarity_measure", Optional[int]),
#                                 ()])



class DrammsXfmAtom(FileAtom):
    pass


DrammsXfmHandler = GenericXfmHandler[NiiAtom, DrammsXfmAtom]


# TODO add more options?
def dramms_to_itk(xfm: DrammsXfmAtom) -> Result[ITKXfmAtom]:
    out_xfm = xfm.newext(".nii.gz")
    return Result(stages=Stages([CmdStage(cmd=['dramms-convert', '-f', 'DRAMMS',
                                               '-i', xfm.path, '-F', 'ITK', '-o', out_xfm.path],
                                          inputs=(xfm,), outputs=(out_xfm,))]),
                  output=out_xfm)


def dramms_invert(defs: DrammsXfmAtom,
                  out_defs: Optional[DrammsXfmAtom] = None):
    out_defs = out_defs or defs.newname_with_suffix("_inverted")

    return Result(stages=Stages([CmdStage(cmd=['dramms-defop', '-i', defs.path, out_defs.path],
                                          inputs=(defs,), outputs=(out_defs,))]),
                  output=out_defs)


# only works on two images ... does using this multiple times introduce resampling error or does the resampling
# error get deferred as with MNI xfmconcat?  In the latter case we should convert to another format to concat
def dramms_combine(t1: DrammsXfmAtom, t2: DrammsXfmAtom, name: Optional[str] = None):
    # TODO this is a hack ... implement more sane naming (factor from xfmconcat?)
    out_xfm = t1.newname(name="concat_of_%s_and_%s" % (t1.filename_wo_ext, t2.filename_wo_ext),
                         subdir="transforms")
    s = CmdStage(cmd=['dramms-combine', '-c', t1.path, t2.path, out_xfm.path],
                 inputs=(t1, t2),
                 outputs=(out_xfm))
    return Result(stages=Stages([s]), output=out_xfm)


# TODO this is foolishly copy-pasted from mincresample_simple; better factoring needed.
def dramms_warp_simple(img: NiiAtom,
                       xfm: DrammsXfmAtom,
                       like: Optional[NiiAtom] = None,
                       extra_flags: Tuple[str] = (),
                       use_nn_interpolation = None,
                       #invert: bool = False,
                       new_name_wo_ext: str = None,
                       subdir: str = None) -> Result[NiiAtom]:
    """
    Resample an image, ignoring mask/labels
    ...
    new_name_wo_ext -- string indicating a user specified file name (without extension)
    """

    if not subdir:
        subdir = 'resampled'

    if not new_name_wo_ext:
        # FIXME the path to `outf` is wrong.  For instance, resampling a mask file ends up in the initial model
        # FIXME directory instead of in the resampled directory.
        # FIXME At the same time, `like.newname(...)` doesn't work either, for similar reasons.
        # FIXME Not clear if there's a general "automagic" fix for this.
        # FIXME Also, using the xfm's filename is wrong, since we might be resampling, e.g., a mask.
        # FIXME We should basically use the same naming scheme as is used to generate the xfm's name but
        # FIXME use the files for resampling, not registration
        outf = img.newname(name=xfm.filename_wo_ext + '-resampled', subdir=subdir)
    else:
        # we have the output filename without extension. This should replace the entire
        # current "base" of the filename.
        outf = img.newname(name=new_name_wo_ext, subdir=subdir)

    stage = CmdStage(
        inputs=(xfm, like, img),
        outputs=(outf,),
        cmd=(['dramms-warp']
             + (['-n'] if use_nn_interpolation else [])
             + (['-transform %s' % xfm.path]) #if xfm is not identity else [])
             + ['-like %s' % like.path, img.path, outf.path]))

    return Result(stages=Stages([stage]), output=outf)

def dramms_warp(img: NiiAtom,  # TODO change to ITKAtom ?!
                xfm: XfmAtom,  # TODO: update to handler?
                like: NiiAtom,
                invert: bool = False,
                use_nn_interpolation = None,
                #interpolation: Interpolation = None,
                #extra_flags: Tuple[str] = (),
                new_name_wo_ext: str = None,
                subdir: str = None,
                postfix: str = None) -> Result[NiiAtom]:


    s = Stages()

    if not subdir:
        subdir = 'resampled'

    # we need to get the filename without extension here in case we have
    # masks/labels associated with the input file. When that's the case,
    # we supply its name with "_mask" and "_labels" for which we need
    # to know what the main file will be resampled as
    if not new_name_wo_ext:
        # FIXME this is wrong when invert=True
        new_name_wo_ext = xfm.filename_wo_ext + '-resampled'

    new_img = s.defer(dramms_warp_simple(img=img, xfm=xfm, like=like,
                                         #extra_flags=extra_flags,
                                         invert=invert,
                                         #interpolation=interpolation,
                                         use_nn_interpolation=use_nn_interpolation,
                                         new_name_wo_ext=new_name_wo_ext,
                                         subdir=subdir))
    new_img.mask = s.defer(dramms_warp_simple(img=img.mask, xfm=xfm, like=like,
                                              #extra_flags=extra_flags,
                                              #interpolation=Interpolation.nearest_neighbour,
                                              use_nn_interpolation=True,
                                              invert=invert,
                                              new_name_wo_ext=new_name_wo_ext + "_mask",
                                              subdir=subdir)) if img.mask is not None else None
    new_img.labels = s.defer(dramms_warp_simple(img=img.labels, xfm=xfm, like=like,
                                                #extra_flags=label_extra_flags,
                                                #interpolation=Interpolation.nearest_neighbour,
                                                use_nn_interpolation=True,
                                                invert=invert,
                                                new_name_wo_ext=new_name_wo_ext + "_labels",
                                                subdir=subdir)) if img.labels is not None else None

    # Note that new_img can't be used for anything until the mask/label files are also resampled.
    # This shouldn't create a problem with stage dependencies as long as masks/labels appear in inputs/outputs of CmdStages.
    # (If this isn't automatic, a relevant helper function would be trivial.)
    # TODO: can/should this be done semi-automatically? probably ...
    return Result(stages=s, output=new_img)


class DRAMMSAlgorithms(itk.Algorithms):
    @staticmethod
    def scale_transform(xfm : XfmHandler, scale, newname_wo_ext) -> XfmAtom:
        raise NotImplementedError
    resample = dramms_warp
    @staticmethod
    def average_transforms(xfms, avg_xfm): raise NotImplementedError


class DRAMMS(NLIN[NiiAtom, DrammsXfmAtom]):

  MultilevelConf = Tuple[str]

  Conf = str

  Algorithms = DRAMMSAlgorithms

  @staticmethod
  def hierarchical_to_single(conf):
      return conf

  @staticmethod
  def accepts_initial_transform():
      return True

  @classmethod
  def parse_protocol_file(cls, filename, resolution):
    c = cls.parse_multilevel_protocol_file(filename, resolution)
    if len(c) > 1:
      warnings.warn("found multiple DRAMMS confs in '%s'; using the last one" % filename)
    return c[-1]

  @classmethod
  def parse_multilevel_protocol_file(cls, filename, resolution):
    # try:
    #   with open(filename, 'r') as f:
    #     return (p.parse_args(l) for l in f.readlines())
    # except SystemExit:
    #   raise ValueError("malformed DRAMMS protocol")
    with open(filename, 'r') as f:
      return tuple(f.readlines())

  @staticmethod
  def get_default_conf(resolution): return ""

  @staticmethod
  def get_default_multilevel_conf(resolution): return [""] * 3

  class ToMinc(ToMinc):
      @staticmethod
      def to_mnc(img): return convert(img, out_ext=".mnc")
      @staticmethod
      def from_mnc(img): return convert(img, out_ext=".nii.gz")
      @staticmethod
      def to_mni_xfm(xfm):
          s = Stages()
          itk_xfm = s.defer(dramms_to_itk(xfm))
          mni_xfm = s.defer(itk_convert_xfm(itk_xfm, out_ext=".xfm"))
          return Result(stages=s, output=mni_xfm)
      @staticmethod
      def from_mni_xfm(xfm): raise NotImplementedError

  @classmethod
  def register(cls,
               source,
               target,
               conf,
               initial_source_transform = None,
               transform_name_wo_ext = None,
               resample_source = True,  # ignored!
               resample_subdir = "resampled"):

      # TODO this stuff is basically stolen from ANTS ... we should make some utility wrappers
      # instead of pasting these boring lines everywhere
      if resample_source and resample_subdir == "tmp":
          trans_output_dir = "tmp"
      else:
          trans_output_dir = "transforms"

      # TODO instead of setting ext here manually, add to Algorithms/Types ... ?
      if transform_name_wo_ext:
          name = os.path.join(source.pipeline_sub_dir, source.output_sub_dir, trans_output_dir,
                              "%s.nii.gz" % (transform_name_wo_ext))
      else:
          name = os.path.join(source.pipeline_sub_dir, source.output_sub_dir, trans_output_dir,
                              "%s_DRAMMS_to_%s.nii.gz" % (source.filename_wo_ext, target.filename_wo_ext))
      out_def = DrammsXfmAtom(name=name, pipeline_sub_dir=source.pipeline_sub_dir, output_sub_dir=source.output_sub_dir)

      out_img = source.newname_with_suffix("_to_%s" % target.filename_wo_ext)

      cmd = (["dramms",
              "--source", source.path, "--target", target.path,
              "--outimg", out_img.path, "--outdef", out_def.path]
             + (["--bt", target.mask.path] if target.mask else [])
             + (["--bs", source.mask.path] if source.mask else [])
             + (["-d", initial_source_transform.path] if initial_source_transform else [])
             + conf.split())
      s = CmdStage(cmd=cmd,
                   inputs=tuple(i for i in (source, initial_source_transform, source.mask, target.mask)
                                if i is not None),
                   outputs=(out_img, out_def))
      return Result(stages=Stages([s]),
                    output=XfmHandler(source=source, target=target, xfm=out_def, resampled=out_img))
             #+ ((flatten(*[
             #     # TODO: ffd spacing
             #     ([] if ... else [])
             #    ])) if conf else []))