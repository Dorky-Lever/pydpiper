#!/usr/bin/env python

### to use this script, run the following:
#$ wget repo.mouseimaging.ca/repo/Pydpiper_test_files/test-data.tar.gz
#$ tar xf test-data.tar.gz test-data
# ... alter the config file in the test-data directory as appropriate ...
#$ test-script.py /path/to/test-data --working_dir=/directory/to/run/pipelines

import argparse
import glob
import os
import re
import subprocess
import sys
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument("test_data_dir", type=str)
parser.add_argument("--working_dir", type=str, default=".")

args = parser.parse_args()

datadir     = os.path.abspath(args.test_data_dir)
workdir     = os.path.abspath(args.working_dir)
atlas_dir   = os.path.join(datadir, "ex-vivo-atlases")
config_file = os.path.join(datadir, "sample.cfg")
num_execs   = len(glob.glob(os.path.join(datadir, "test-images/*mnc")))
MBM_dir     = os.path.join(workdir, "MBM-pipeline")
MAGeT_dir   = os.path.join(workdir, "MAGeT-pipeline")
if not os.path.isdir(MBM_dir):
    os.makedirs(MBM_dir) 
if not os.path.isdir(MAGeT_dir):
    os.makedirs(MAGeT_dir) 

#TODO don't duplicate config file, atlas, protocol unnecessarily (user has pydpiper source available ...)
#TODO fix config file locations in MBM, MAGeT (env var?)

MBM_name = "MBM_test"
os.chdir(MBM_dir)
files = glob.glob("{datadir}/test-images/*.mnc".format(**vars()))
subprocess.check_call("""MBM.py
  --pipeline-name={MBM_name}
  --num-executors={num_execs}
  --verbose
  --init-model={datadir}/Pydpiper-init-model-basket-may-2014/basket_mouse_brain.mnc
  --config-file={config_file}
  --lsq6-large-rotations
  --no-run-maget
  --maget-no-mask
  --lsq12-protocol={datadir}/default_linear_MAGeT_prot.csv
  --files """.format(**vars()).split() + files)

os.chdir(MAGeT_dir)
MAGeT_name = "MAGeT_test"
subprocess.check_call("""MAGeT.py 
  --verbose
  --no-pairwise
  --registration-method=minctracc
  --pipeline-name={MAGeT_name}
  --num-executors={num_execs}
  --atlas-library={atlas_dir}
  --config-file={config_file}
  --lsq12-protocol={datadir}/default_linear_MAGeT_prot.csv
  --nlin-protocol={datadir}/default_nlin_MAGeT_minctracc_prot.csv
  --masking-nlin-protocol={datadir}/default_nlin_MAGeT_minctracc_prot.csv
  --files {MBM_dir}/{MBM_name}_nlin/{MBM_name}-nlin-3.mnc""".format(**vars()).split())

# create csv file of determinant files, as per wiki.mouseimaging.ca/display/MICePub/Pydpiper+Virtual+Machine:
os.chdir(workdir)
with open(os.path.join(workdir, "absolute_jacobians_and_genotypes.csv"), 'w') as f:
    f.write("absolute_jacobians,genotype\n")
    for file in glob.iglob("{MBM_dir}/{MBM_name}_processed/*".format(**vars())):
        base = os.path.basename(file)
        if re.search("deformed", base):
            type="mutant"
        else:
            type="wt"
        f.write("""{file}/stats-volumes/{base}_N_I_lsq6_lsq12_and_nlin_inverted_displ_log_det_abs.mnc,{type}\n""".format(**vars()))

script = """
  library(RMINC)
  gf <- read.csv("{workdir}/absolute_jacobians_and_genotypes.csv")
  volume_striatum <- anatGetAll(gf$absolute_jacobians,
                                atlas="{MAGeT_dir}/{MAGeT_name}_processed/{MBM_name}-nlin-3/voted.mnc",
                                defs="{datadir}/mapping_for_striatum.csv")
  result <- mapply(mean, split(apply(volume_striatum, 1, sum), gf$genotype))
  result <- as.list(result)
  frac   <- result$mutant/result$wt
  print(paste("In these test data, we've introduced a 10% decrease in the volume of the striatum in the mutants. The difference we found using the image registration software is: ", format((frac - 1) * 100,digits=3), "%.", sep=""))
  if ((frac < 0.875) || (frac > 0.925)) stop("Failed: unexpected result... We expect the volume difference as estimated by the registration software to lie between -12.5% and -7.5%.") else print("Succeeded! We expect the registration procedure to underestimate the true underlying change (see https://www.ncbi.nlm.nih.gov/pubmed/23756204). The difference found lies within our acceptance boundaries: -12.5% and -7.5%.")
""".format(**vars())

with tempfile.NamedTemporaryFile(mode='w') as f:
    f.write(script)
    f.flush()
    sys.exit(subprocess.call(['Rscript', f.name]))
