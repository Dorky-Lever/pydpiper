from pydpiper.pipeline import Pipeline, CmdStage, InputFile, OutputFile, LogFile
from pydpiper_apps.minc_tools.registration_functions import isFileHandler
import pydpiper.file_handling as fh
from optparse import OptionGroup
import sys
import Pyro

Pyro.config.PYRO_MOBILE_CODE=1

def addStatsOptions(parser):
    group = OptionGroup(parser, "Statistics options", 
                        "Options for calculating statistics.")
    group.add_option("--stats-kernels", dest="stats_kernels",
                      type="string", default="1.0,0.5,0.2,0.1", 
                      help="comma separated list of blurring kernels for analysis. Default is: 1.0,0.5,0.2,0.1")
    parser.add_option_group(group)

class StatsGroup:
    """This group saves the key output from each instance for CalcStats, 
       so it can easily be retrieved later."""
    def __init__(self):
        self.jacobians = {}
        self.scaledJacobians = {}        

class CalcStats:
    def __init__(self, inputFH, targetFH, blurs):
        self.p = Pipeline()
        self.inputFH = inputFH
        self.targetFH = targetFH
        self.blurs = blurs
        self.statsGroup = StatsGroup()
        self.setupXfms()
        
    def setupXfms(self):
        self.xfm = self.inputFH.getLastXfm(self.targetFH)
        if not self.xfm:
            print "Cannot calculate statistics. No transform between input and target specified."
            sys.exit()
    
    def fullStatsCalc(self):
        self.linAndNlinDisplacement()
        self.calcDetAndLogDet()  
    
    def calcFullDisplacement(self):
        """Calculates the full displacement without removing the linear part"""
        fullDisp = mincDisplacement(self.inputFH, self.targetFH, self.xfm)
        self.p.addStage(fullDisp)
        self.fullDisp = fullDisp.outputFiles[0]
        
    def linAndNlinDisplacement(self):
        """Need to fill in this function and combine with version in CalcChainStats
           Main diffs: 1. target is common space, so inv_nlin used for full nlin (as in MBM)
           2. Need to average and centre displacement after calculating (this might be separate)"""
        
        pass
        
         
    def calcDetAndLogDet(self, useFullDisp=False):  
        #Lots of repetition here--let's see if we can't make some functions.
        """useFullDisp indicates whether or not """ 
        if useFullDisp:
            dispToUse = self.fullDisp
        else:
            dispToUse = self.nlinDisp
        for b in self.blurs:
            """Calculate smoothed deformation field"""
            fwhm = "--fwhm=" + str(b)
            outputBase = fh.removeBaseAndExtension(dispToUse).split("_displacement")[0]
            outSmooth = fh.createBaseName(self.inputFH.tmpDir, 
                                       outputBase + "_smooth_displacement_fwhm" + str(b) + ".mnc")
            cmd = ["smooth_vector", "--clobber", "--filter", fwhm, 
                   InputFile(dispToUse), OutputFile(outSmooth)]
            smoothVec = CmdStage(cmd)
            smoothVec.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, outSmooth)))
            self.p.addStage(smoothVec)
            
            """Calculate the determinant, then add 1 (per mincblob weirdness)"""
            outputDet = fh.createBaseName(self.inputFH.tmpDir, 
                                          outputBase + "_determinant_fwhm" + str(b) + ".mnc")
            cmd = ["mincblob", "-clobber", "-determinant", InputFile(outSmooth), OutputFile(outputDet)]
            det = CmdStage(cmd)
            det.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, outputDet)))
            self.p.addStage(det)
            outDetShift = fh.createBaseName(self.inputFH.tmpDir, 
                                          outputBase + "_det_plus1_fwhm" + str(b) + ".mnc")
            cmd = ["mincmath", "-clobber", "-2", "-const", str(1), "-add", 
                   InputFile(outputDet), OutputFile(outDetShift)]
            det = CmdStage(cmd)
            det.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, outDetShift)))
            self.p.addStage(det)
            
            """Calculate log determinant (jacobian) and add to statsGroup."""
            outLogDet = fh.createBaseName(self.inputFH.statsDir, 
                                          outputBase + "_log_determinant_fwhm" + str(b) + ".mnc")
            cmd = ["mincmath", "-clobber", "-2", "-log", InputFile(outDetShift), OutputFile(outLogDet)]
            det = CmdStage(cmd)
            det.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, outLogDet)))
            self.p.addStage(det)
            self.statsGroup.jacobians[b] = outLogDet
            
            """If self.linearXfm present, calculate scaled log determinant (scaled jacobian) and add to statsGroup"""
            if not useFullDisp:
                #MF TODO: Depending on which space inputs are in, may need to handle additional lsq12 transform, as in build-model
                outLogDetScaled = fh.createBaseName(self.inputFH.statsDir, 
                                                    outputBase + "_log_determinant_scaled_fwhm" + str(b) + ".mnc")
                cmd = ["scale_voxels", "-clobber", "-invert", "-log", 
                       InputFile(self.linearXfm), InputFile(outLogDet), OutputFile(outLogDetScaled)]
                det = CmdStage(cmd)
                det.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, outLogDetScaled)))
                self.p.addStage(det)
                self.statsGroup.scaledJacobians[b] = outLogDetScaled
            else:
                self.statsGroup.scaledJacobians = None

class CalcChainStats(CalcStats):
    """This class calculates multiple displacement fields, jacobians and scaled jacobians"""
    def __init__(self, inputFH, targetFH, blurs):
        CalcStats.__init__(self, inputFH, targetFH, blurs)
        
    def linAndNlinDisplacement(self):    
        """The function calculates both the linear and nonlinear
           portions of the displacement, in order to find 
           pure nonlinear. Input is the commonSpace, so the pure
           nonlinear displacement will point from input to target.
        
           This is opposite from the standard stats class, where
           the common space is the target
           
        """
        
        """Calculate linear Part of Nlin xfm and displacement using inverted xfm"""
        lpnl = linearPartofNlin(self.inputFH, self.targetFH)
        self.p.addStage(lpnl)
        self.linearXfm = lpnl.outputFiles[0]
        invXfmBase = fh.removeBaseAndExtension(self.linearXfm).split(".xfm")[0]
        invXfm = fh.createBaseName(self.inputFH.transformsDir, invXfmBase + "_inverse.xfm")
        cmd = ["xfminvert", "-clobber", InputFile(self.linearXfm), OutputFile(invXfm)]
        invertXfm = CmdStage(cmd)
        invertXfm.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, invXfm)))
        self.p.addStage(invertXfm)
        linDisp = mincDisplacement(self.inputFH, self.targetFH, invXfm)
        self.p.addStage(linDisp)
        self.linDisp = linDisp.outputFiles[0]
        
        """Calculate full displacement from target to source
           invert lastXfm and use this to calculate"""
        self.calcFullDisplacement()
        
        """Calculate nlin displacement from source to target"""
        nlinBase = fh.removeBaseAndExtension(self.xfm) + "_nlin_displacement.mnc"
        self.nlinDisp = fh.createBaseName(self.inputFH.tmpDir, nlinBase)
        cmd = ["mincmath", "-clobber", "-add", InputFile(self.fullDisp),
               InputFile(self.linDisp), OutputFile(self.nlinDisp)]
        mincmath = CmdStage(cmd)
        mincmath.setLogFile(LogFile(fh.logFromFile(self.inputFH.logDir, self.nlinDisp)))
        self.p.addStage(mincmath)

class linearPartofNlin(CmdStage):
    def __init__(self, inputFH, targetFH, defaultDir="transforms"):
        CmdStage.__init__(self, None)
        
        try:  
            if isFileHandler(inputFH, targetFH):
                self.inFile = inputFH.getLastBasevol()  
                self.mask = inputFH.getMask()   
                self.xfm = inputFH.getLastXfm(targetFH)     
                self.outfile = self.setOutputFile(inputFH, defaultDir)
                self.logFile = fh.logFromFile(inputFH.logDir, self.outfile)
            else:
                print ("linear part of nlin currently only works using file handlers. "
                       "Exception being raised.")
                raise
    
        except:
            print "Failed in putting together linearPartofNlin command"
            print "Unexpected error: ", sys.exc_info()
            
        self.addDefaults()
        self.finalizeCommand()
        self.setName()
        
    def addDefaults(self):
        self.inputFiles += [self.inFile, self.xfm]   
        self.outputFiles += [self.outfile]       
        self.cmd += ["lin_from_nlin",
                     "-clobber", "-lsq12"] 
        if self.mask: 
            self.inputFiles += [self.mask]
            self.cmd += ["-mask", self.mask]
                 
    def finalizeCommand(self):
        self.cmd += [self.inFile, self.xfm, self.outfile]   
    def setName(self):
        self.name = "lin_from_nlin " 
    def setOutputFile(self, inFile, defaultDir):
        outDir = inFile.setOutputDirectory(defaultDir)
        outBase = (fh.removeBaseAndExtension(self.xfm) + "_linear_part.xfm")
        outputFile = fh.createBaseName(outDir, outBase)
        return(outputFile)  

class mincDisplacement(CmdStage):
    def __init__(self, inputFH, targetFH, transform=None, defaultDir="tmp"):
        CmdStage.__init__(self, None)
        try:  
            if isFileHandler(inputFH, targetFH):
                self.inFile = inputFH.getLastBasevol()  
                self.targetFile = targetFH.getLastBasevol()
                if not transform:
                    self.xfm = inputFH.getLastXfm(targetFH)
                else:
                    self.xfm = transform
                self.outfile = self.setOutputFile(inputFH, defaultDir)
                self.logFile = fh.logFromFile(inputFH.logDir, self.outfile)
            else:
                print ("minc_displacement only works using file handlers. "
                       "Exception being raised.")
                raise
    
        except:
            print "Failed in putting together minc_displacement command"
            print "Unexpected error: ", sys.exc_info()
            
        self.addDefaults()
        self.finalizeCommand()
        self.setName()
        
    def addDefaults(self):
        self.inputFiles += [self.inFile, self.xfm]   
        self.outputFiles += [self.outfile]       
        self.cmd += ["minc_displacement",
                     "-clobber"] 
                 
    def finalizeCommand(self):
        self.cmd += [self.inFile, self.xfm, self.outfile]    
    def setName(self):
        self.name = "minc_displacement " 
    def setOutputFile(self, inFile, defaultDir):
        outDir = inFile.setOutputDirectory(defaultDir)
        outBase = (fh.removeBaseAndExtension(self.xfm) + "_displacement.mnc")
        outputFile = fh.createBaseName(outDir, outBase)
        return(outputFile)  
    