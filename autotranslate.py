#!/usr/local/bin/python3
# assumes Python 3.7.x
import deepl                                     # pip3 install --upgrade deepl  # https://github.com/DeepLcom/deepl-python#configuration
from pypdf import PdfMerger, PdfReader           # pip3 install pypdf
from unidecode import unidecode                  # pip3 install unidecode
from dateutil.relativedelta import relativedelta # pip3 install python-dateutil
from datetime import datetime,timedelta  # https://docs.python.org/3/library/datetime.html
import logging, logging.handlers # https://docs.python.org/3/howto/logging.html
import os
import string
import sys
import time
import re



# ##################################################################
# FUNCTIONS
# ##################################################################

def translateDocument(inputDocument, outputDocument, targetLang):
  # Translate a formal document
  # originally copied from https://github.com/DeepLcom/deepl-python#translating-documents

  logger.info(f'Uploading file to DeepL web API translation service.')
  logger.debug(f"\tinput document:  {inputDocument}")
  logger.debug(f"\toutput document: {outputDocument}")
  logger.debug(f"\ttarget language: {targetLang}")
  
  global wasQuotaExceeded
  retVal = False
  try:
    # Using translate_document_from_filepath() with file paths 
    translator.translate_document_from_filepath(
        inputDocument,
        outputDocument,
        target_lang=targetLang,
        formality="prefer_more"
    )

    # # Alternatively you can use translate_document() with file IO objects
    # with open(inputDocument, "rb") as in_file, open(outputDocument, "wb") as out_file:
        # translator.translate_document(
            # in_file,
            # out_file,
            # target_lang=targetLang,
            # formality="prefer_more"
        # )

    logger.info(f'Translation complete!')
    retVal = True
    
  except deepl.DocumentTranslationException as error:
    # If an error occurs during document translation after the document was
    # already uploaded, a DocumentTranslationException is raised. The
    # document_handle property contains the document handle that may be used to
    # later retrieve the document from the server, or contact DeepL support.
    logger.error(f"Error after uploading to translation API.")
    # logger.error(f"{error}")
    # get various pieces of data from the error
    errorType = type(error)                     # here it'll only return DocumentTranslationException, but originally it was in the just Exception section
    docHNDL = str(error.document_handle)        # pull out the document ID & Key, in case the user needs to use them outside this script.
    tmpStr = ', document handle: ' + docHNDL
    errMsg = str(error).replace(tmpStr, '.')    # DocumentTranslationException doesn't have a `message` attribute, so we'll make one
    logger.error(f"\t{errMsg}")
    p = re.compile('Document ID:\s+(\w+), key:\s+(\w+)')  # finish pulling the ID and key
    m = p.match(docHNDL)
    docID = m[1]
    docKey = m[2]
    logger.error(f"\tDocument ID:  {docID}")
    logger.error(f"\tDocument Key: {docKey}")
    if errMsg == "Quota for this billing period has been exceeded.": # the error was that the quota was not empty, but still too low (note having to add the `.` because we added it in replace() above
      wasQuotaExceeded = True
  except Exception as error:
    # Errors during upload raise a DeepLException
    logger.error("Unknown error occurred during the translation process.")
    errorType = type(error)
    logger.debug(f"{errorType}")
    logger.error(f"{error}")
   
  return retVal

def getUsage():
  # @return The number of characters left in your usage allowance.
  # originally copied from https://github.com/DeepLcom/deepl-python#translating-documents
  charsLeft = 0
  try:
    usage = translator.get_usage()
    
    if usage.any_limit_reached:
      logger.error('Translation limit reached.')
      if usage.character.valid:
        logger.error(f'\tCharacter limit: {usage.character.limit:,}')
      if usage.document.valid:
        logger.error(f'\tDocument limit: {usage.document.limit:,}')
      return 0 # can NOT translate

    if usage.character.valid:
      logger.info(f"Character usage: {usage.character.count:,} of {usage.character.limit:,}")
      charsLeft = usage.character.limit - usage.character.count

    if usage.document.valid:
      logger.info(f"Document usage: {usage.document.count:,} of {usage.document.limit:,}")
      if charsLeft == 0:  # only bother to estimate if (usage.character.valid == False)
        avgCharPerDoc = 3000 * 3
        docsLeft = usage.document.limit - usage.document.count
        charsLeft = docsLeft * docsLeft
        
    logger.info(f"Characters left: {charsLeft:,}")
  except Exception as error:
    logger.warning(f"Unexpected error occurred when trying to determine the API usage allowance.")
    logger.warning(f"\t{error}")
  return charsLeft # can still translate

def appendPDFs(firstPDF, secondPDF, outputPDF):
  # combine PDFs into a single PDF
  logger.info('Appending the translated PDF to the end of the original PDF.')
  pdfs = [firstPDF, secondPDF] # I could input a list as a function argument, but I only have 2 PDFs so I made the input args explicit
  merger = PdfMerger()
  
  try:
    for pdf in pdfs:
      merger.append(pdf)
    merger.write(outputPDF)
    merger.close()
    logger.info('\tPDF merger successful.')
    return True
  except Exception as error:
    logger.error("Unknown error occurred during the PDF merging process.")
    logger.error(f"{error}")
    return False

def getSafeFilename(inputFilename):
  ## Make a file name that only contains safe characters  
  # @param inputFilename A filename containing illegal characters  
  # @return A filename containing only safe characters  

  # Set here the valid chars (note the lack of <space> as a valid char)
  safechars = string.ascii_letters + string.digits + "-_."
  try:
      # trim whitespace off the edges of the filename
      fileRoot = os.path.splitext(inputFilename)[0].strip()
      fileExten = os.path.splitext(inputFilename)[1]
      inputFilename = fileRoot  + fileExten
      
      # convert spaces to underscores
      inputFilename = inputFilename.replace(" ", "_")
      
      # replace non-ASCII chars with ASCII ones (e.g., ø to o, æ to ae)
      # carve out special characters that I don't think unidecode() does well
      inputFilename = inputFilename.replace("Å", "Aa")
      inputFilename = inputFilename.replace("å", "aa")
      inputFilename = unidecode(inputFilename)
      
      # now brutally remove any non-ASCII, un-allowed characters
      filteredList = list(filter(lambda c: c in safechars, inputFilename))
      filterStr = ''.join(filteredList)
      return filterStr
  except:
      return ""
  pass 
    
def renameToSafeFilename(directory, oldFilename, newFilename):
  # newFilename = getSafeFilename(oldFilename)
  if oldFilename == newFilename:
    return oldFilename # exit early if the same filenames are used

  # make full path names
  oldFullname = os.path.join(directory, oldFilename)
  newFullname = os.path.join(directory, newFilename)

  logger.info(f'Renaming file to a cleaner filename.')
  logger.info(f"\told: {oldFilename}")
  logger.info(f"\tnew: {newFilename}")

  if os.path.isfile(newFullname):
    logger.error(f"New filename already exists.")
    return "" # blank return value indicates an error occurred
  else:
    try:
      os.rename(oldFullname, newFullname)
      logger.info(f'Renaming successful.')
      return newFilename
    except OSError as error:
      logger.error(f"Unknown error when renaming the file.")
      logger.error(f"{error}")
      return "" # blank return value indicates an error occurred
      
def deleteFile(filename):
  # deletes a file, with some error checking 
  logger.info(f"Attempting to delete file:")
  logger.info(f"\t{filename}")
  if os.path.exists(filename):
    try:
      os.remove(filename)
      logger.info(f"\tFile successfully deleted.")
      return True
    except:
      logger.warning(f"\tError while trying to delete file: {filename}")
      return False
  else:
    logger.warning(f"\tFile did not exist in the first place. Nothing to do.")
    return True
  
def getCharCountOfPDF(filename):
  # attempts to estimate how many characters are in a PDF
  # does not work unless the PDF has been OCRed or otherwise includes text (e.g., printed from a Word document)
  saved_text = ""
  reader = PdfReader(filename)
  numPages = len(reader.pages)
  for page_number in range(numPages):
    page = reader.pages[page_number]
    # Once you have your Page object, call its extractText() method to return a string of the page’s text ❸. The text extraction isn’t perfect. 
    page_content = page.extract_text()
    saved_text = saved_text + page_content
  # print(saved_text)
  # print("-----------------------------")
  charCount = len(saved_text)
  logger.debug(f'Estimated number of characters in the file: {charCount:,}')
  return charCount
  
def exitProgram(msg=""):
  # exits the program, was going to do more but changed my mind
  sys.exit(msg)   # exit the program
  return
  
def sleepWithACountdown(secs, isTop=True):
  # a rather involved subroutine to put the program to sleep and nicely output a countdown to the log.

  # set some constants
  days_inSecs    = 1 * 24 * 60 * 60
  hours_inSecs   = 1      * 60 * 60
  minutes_inSecs = 1           * 60
  
  if isTop: # isTop is there to handle recursive function issues
    # create a output message
    delayStr = []
    totalSleepTime = secs
    if totalSleepTime > days_inSecs:
      (tmpVal, totalSleepTime) = divmod(totalSleepTime, days_inSecs)
      delayStr.append(f"{tmpVal} day(s)")
    if totalSleepTime > hours_inSecs:
      (tmpVal, totalSleepTime) = divmod(totalSleepTime, hours_inSecs)
      delayStr.append(f"{tmpVal} hour(s)")
    if totalSleepTime > minutes_inSecs:
      (tmpVal, totalSleepTime) = divmod(totalSleepTime, minutes_inSecs)
      delayStr.append(f"{tmpVal} minute(s)")
    if totalSleepTime > 0:
      delayStr.append(f"{totalSleepTime} second(s)")
    tmpStr = ", ".join(delayStr)
    logger.info(f"Putting the program to sleep for {tmpStr}.")
    logger.info(f"Counting down the sleep period ...")
    

  # turn off the logger's automatic NewLine for this subroutine
    # origTerminator_CH = consoleHandler.terminator
    # consoleHandler.terminator = ""
    if not (globalFileHandler is None): # note the creation of the GFL may have failed
      origTerminator_GFL = globalFileHandler.terminator
      globalFileHandler.terminator = ""
      globalFileHandler.setFormatter(logging.Formatter('%(message)s')) # turn off the custom formatting too

  
  # an array of granularity levels
  # period_letter, period_delay, period_minimum, secs_in_the_letter
  periods = [
    ('d',        days_inSecs,   2.1 * days_inSecs,      days_inSecs),
    ('h',   5 * hours_inSecs,   9.9 * hours_inSecs,     hours_inSecs),
    ('h',       hours_inSecs,         hours_inSecs,     hours_inSecs),
    ('m', 5 * minutes_inSecs,   9.9 * minutes_inSecs,   minutes_inSecs),
    ('m',     minutes_inSecs,         minutes_inSecs,   minutes_inSecs),
    ('s',                  5,    9,                    1),
    ('s',                  1,    -1,                    1)
  ]
  
  # select which granularity we are at
  for period_letter, period_delay, period_minimum, secs_in_the_letter in periods:
    if secs > period_minimum:
      delayPeriod = period_delay
      delayLetter = period_letter
      granularityThreshold = period_minimum
      amountOfSecs = secs_in_the_letter
      break
      
  # conduct the countdown
  remaingSecs = secs
  while remaingSecs > 0:
    if remaingSecs < granularityThreshold:
      sleepWithACountdown(remaingSecs, False) # the easiest way to change the delayLetter to the next level of granularity is to just recursively call the function
      remaingSecs = 0
    elif remaingSecs >= delayPeriod:
      (i, remainder) = divmod(remaingSecs, amountOfSecs)
      logger.info(f"{i}{delayLetter} ... ")
      remaingSecs = remaingSecs - delayPeriod
      time.sleep(delayPeriod)
    elif remaingSecs < delayPeriod and remaingSecs > 0:
      logger.info(f"{remaingSecs}{delayLetter} ... ")
      time.sleep(remaingSecs)
      remaingSecs = 0
    else:
      remaingSecs = 0


  if isTop:
    # turn on the logger's automatic NewLine
    # consoleHandler.terminator = origTerminator_CH
    if not (globalFileHandler is None):
      globalFileHandler.terminator = origTerminator_GFL

  
    # write final line (with restored NewLine)
    logger.info(f"0s")

    # turn on the logger's formatting
    if not (globalFileHandler is None):
      globalFileHandler.setFormatter(globalFileFormatter)
  return

def numbOfSecsTillRenewal():
  # calculates the number of seconds until the next renewal of the usage allowance.  Default is 7 days, or ENV{DEEPL_USAGE_RENEWAL_DAY} minus Now().
  
  # default value
  waitUntilNewUsage_Days = 7 # default period to wait
  waitUntilNewUsage_Sec = waitUntilNewUsage_Days * 24 * 60 * 60

  # calculate the time to wait, if the usageRenewalDay value is set (default value is 0, which fails the if statement)
  logger.debug(f"\tnumbOfSecsTillRenewal() debug output ...")
  logger.debug(f"\tusageRenewalDay:         {usageRenewalDay}")
  if usageRenewalDay >= 1 and usageRenewalDay <= 31 : # use a more accurate day for the sleep time
    now_dateTime  = datetime.now()
    lastRenewal_dateTime = datetime(now_dateTime.year, now_dateTime.month, usageRenewalDay)
    nextRenewal_dateTime = lastRenewal_dateTime + relativedelta(days=1, months=1) # add 1 to the renewal date to avoid any corner case issues
    duration_dateTime = nextRenewal_dateTime - now_dateTime
    waitUntilNewUsage_Sec = duration_dateTime.total_seconds()

    logger.debug(f"\tnow_dateTime = {now_dateTime}")
    logger.debug(f"\tnextRenewal_dateTime = {nextRenewal_dateTime}")
    logger.debug(f"\tduration_dateTime = {duration_dateTime}")
    logger.debug(f"\twaitUntilNewUsage_Sec = {waitUntilNewUsage_Sec}")
  return waitUntilNewUsage_Sec

  
# ##################################################################
# MAIN
# ##################################################################


# -------------------------------------------------------------
# setup logging
logger = logging.getLogger(os.path.basename(__file__))
logger.setLevel(logging.DEBUG) # sets the level below which _no_ handler may go
# setup the STDOUT log
consoleHandler = logging.StreamHandler() # well this is annoying.  StreamHandler is logging.* while newer handlers are logging.handlers.*
consoleHandler.setLevel(logging.DEBUG)
consoleFormatter = logging.Formatter('%(message)s')
consoleHandler.setFormatter(consoleFormatter)
logger.addHandler(consoleHandler)

# -------------------------------------------------------------
# initialize configuration variables
isInDocker      = bool(os.getenv("AM_I_IN_A_DOCKER_CONTAINER",0))            # (hidden) Only used to set variables to different values if you're not in a Docker container
  # export DEEPL_SERVER_URL="http://localhost:3000"
  # see mock DeepL server at https://github.com/DeepLcom/deepl-mock
  # and the pre-made Docker image at https://hub.docker.com/r/thibauddemay/deepl-mock
auth_key        =     os.getenv("DEEPL_AUTH_KEY", "ThisIsABogusTestingKey")  # (mandatory) get your key at https://www.deepl.com/account/usage
serverURL       =     os.getenv("DEEPL_SERVER_URL", "")                      # (optional) "" is the actual DeppL server, anything else is for testing 
targetLang      =     os.getenv("DEEPL_TARGET_LANG","EN-US")                 # (near mandatory)  The language isn't really changeable on the fly.  Assumes you want all your files translated to a common language
checkPeriodMin  = int(os.getenv("CHECK_EVERY_X_MINUTES",15))                 # (optional) How often you want the inputDir scanned for new files
usageRenewalDay = int(os.getenv("DEEPL_USAGE_RENEWAL_DAY",0))                # (optional) If you put the day of the month your DeepL allowance resets, then the expired usage sleeping will be more accurate.  
if isInDocker:
  inputDir  = "/inputDir/"   # mapped via Docker
  outputDir = "/outputDir/"  # mapped via Docker
  logDir    = "/logDir/"     # mapped via Docker
  tmpDir    = "/tmpDir/"     # hidden from outside Docker
else:
  inputDir  = "/mnt/Emby/0_not_media/translation_scripts/inputDir/"   # mapped via Docker
  outputDir = "/mnt/Emby/0_not_media/translation_scripts/outputDir/"  # mapped via Docker
  logDir    = "/mnt/Emby/0_not_media/translation_scripts/logDir/"     # mapped via Docker
  tmpDir    = "/mnt/Emby/0_not_media/translation_scripts/tmpDir/"     # hidden from outside Docker
  # variables shown to outside world
scriptVersion = "2.1.1b"
  # internal variables
wasQuotaExceeded = False
checkPeriodSec = checkPeriodMin * 60


# setup the global log file
try:
  globalLogFile = os.path.join(logDir, "_autotranslate.log")
  globalFileHandler = logging.handlers.RotatingFileHandler(globalLogFile ,'a',34359738368,3) # filename, append, number of bytes max, number of logs max
  globalFileHandler.setLevel(logging.INFO)
  globalFileFormatter = logging.Formatter('%(asctime)s - %(levelname)7s - %(message)s')
  globalFileHandler.setFormatter(globalFileFormatter)
  logger.addHandler(globalFileHandler)
  globalFileHandler.setFormatter(logging.Formatter('%(message)s')) # turn off the custom formatting too
  logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
  globalFileHandler.setFormatter(globalFileFormatter) # turn the formatting back on
  logger.info(f"Creating global log file!")
  logger.info(f"\tGlobal Log file: {globalLogFile}")
except Exception as error:
  globalFileHandler = None # assign the None value if fileHandler failed
  logger.info(f"") # start on a fresh line (in-case the server crashed mid-line the previous run)
  logger.warning(f"Unable to write to global log file!")
  logger.warning(f"\tGlobal Log file: {globalLogFile}")
  logger.warning(f"\t{error}")

# spit out some debug info
if True:  # really just here for indentation and code folding purposes
  logger.debug(f"Configuration Variables are ...")
  tmpVarA = os.environ.get("DEEPL_AUTH_KEY")
  tmpVarB = os.environ.get("DEEPL_SERVER_URL")
  tmpVarC = os.environ.get("DEEPL_TARGET_LANG")
  tmpVarD = os.environ.get("CHECK_EVERY_X_MINUTES")
  tmpVarE = os.environ.get("DEEPL_USAGE_RENEWAL_DAY")
  # logger.debug(f"\tDEEPL_AUTH_KEY:          {tmpVarA}")
  # logger.debug(f"\tauth_key:                {auth_key}")
  logger.debug(f"\tDEEPL_SERVER_URL:        {tmpVarB}")
  logger.debug(f"\tserverURL:               {serverURL}")
  logger.debug(f"\tDEEPL_TARGET_LANG:       {tmpVarC}")
  logger.debug(f"\ttargetLang:              {targetLang}")
  logger.debug(f"\tCHECK_EVERY_X_MINUTES:   {tmpVarD}")
  logger.debug(f"\tcheckPeriodMin:          {checkPeriodMin}")
  logger.debug(f"\tDEEPL_USAGE_RENEWAL_DAY: {tmpVarE}")
  logger.debug(f"\tusageRenewalDay:         {usageRenewalDay}")
  logger.debug(f"\tinputDir:       {inputDir}")
  logger.debug(f"\toutputDir:      {outputDir}")
  logger.debug(f"\tlogDir:         {logDir}")
  logger.debug(f"\ttmpDir:         {tmpDir}")
  logger.debug(f"")
  logger.debug(f"\tScript name:             {__file__}")
  logger.debug(f"\tScript version:          {scriptVersion}")
  thisFilesLastModDate = datetime.fromtimestamp(os.path.getmtime(__file__))
  logger.debug(f"\tScript last modified:    {thisFilesLastModDate}")

# check that the directories are OK
if not os.access(inputDir, os.R_OK): # it would be best if you write/delete from inputDir, but it is not fatal if you can't
  logger.error(f"Unable to read from input directory!")
  logger.error(f"\t{inputDir}")
  logger.error(f"FATAL ERROR!  Closing Program!")
  exitProgram()
if not os.access(outputDir, os.W_OK):
  logger.error(f"Unable to write to output directory!")
  logger.error(f"\t{outputDir}")
  logger.error(f"FATAL ERROR!  Closing Program!")
  exitProgram()
if not os.access(tmpDir, os.W_OK):
  logger.error(f"Unable to write to temporary directory!")
  logger.error(f"\t{tmpDir}")
  logger.error(f"FATAL ERROR!  Closing Program!")
  exitProgram()
if not os.access(logDir, os.W_OK):
  logger.warning(f"Unable to write to log file directory!")
  logger.warning(f"\t{logDir}")
  logger.warning(f"Program will still continue, but this should be attended to.")


# -------------------------------------------------------------
# establish connection with API server
try:
  logger.info(f"Attempting to establish communication with the Web API server.")
  if serverURL != "":
    logger.info(f"\tUsing custom Web API server: {serverURL}") # keep as INFO as it is such a rare occurrence that the event should be noted in the log
    translator = deepl.Translator(auth_key, server_url=serverURL)
  else:
    logger.debug(f"\tUsing normal Web API server.")
    translator = deepl.Translator(auth_key)
except Exception as error:
  logger.error(f"Critical error occurred when trying to establish communication with the Web API server.")
  logger.error(f"Closing program!")
  logger.error(f"\t{error}")
  exitProgram("Exiting program.")   # exit the program/loop


# -------------------------------------------------------------
# process any files in the directory
logger.info(f'----------------------------------------') # added separator line to global log
while True: # loop forever

  # determine if there is any usage allowance left before moving on to processing files
  logger.info(f"Check API usage allowance.")
  if wasQuotaExceeded:  # allowance is non-zero but too low to do documents
    wasQuotaExceeded = False # clear the flag set during a previous attempt at translating a document (i.e., quota not empty but too low to process documents)
    # calculate the time to wait
    sleepWithACountdown(numbOfSecsTillRenewal())
    continue # restart while loop
  else:  # check if the usage is zero
    try:
      usage = translator.get_usage()
      if usage.any_limit_reached: # no more allowance this month, or too low too do documents.
        # calculate the time to wait
        sleepWithACountdown(numbOfSecsTillRenewal())
        continue # restart while loop
    except Exception as error:
      logger.warning(f"Unexpected error occurred when trying to determine the API usage allowance.")
      logger.warning(f"\t{error}")

  # get every PDF file in inputDir; send it off for translation; output the result in outputDir
  for file in os.listdir(os.fsencode(inputDir)):
      filename = os.fsdecode(file)
      if filename.lower().endswith(".pdf"): 

        # clean the filename to avoid any un-safe chars that would prevent us from uploading the file to the web API
        filename = getSafeFilename(filename)
        if filename == "":  # a blank filename is the result a failed cleaning
          # report the error and skip
          logger.info(f'Processing file: {os.path.join(inputDir, os.fsdecode(file))}') # note the usage on the un-cleaned file name for this log entry
          logger.error(f'Unable to properly clean the filename to allow uploading to the Web API.')
          logger.error(f'Skipping file.')
        else:
          # create variables for all the files
          inputFile  = os.path.join(inputDir, filename)
          outputFile = os.path.join(outputDir, filename)
          tmpFile    = os.path.join(tmpDir, filename)
          logFile    = os.path.join(logDir,os.path.splitext(filename)[0] + '.log')

          # setup the individual (non-global) log file
          try:
            fileHandler = logging.FileHandler(logFile)
            fileHandler.setLevel(logging.DEBUG)
            fileHandler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)7s - %(message)s'))
            logger.addHandler(fileHandler)
          except Exception as error:
            fileHandler = None # assign the None value if fileHandler failed
            logger.warning(f"Unable to write to individual log file!")
            logger.warning(f"\tIndividual Log file: {logFile}")
            logger.warning(f"\t{error}")

          # report start of processing
          logger.info(f'Processing file: {os.path.join(inputDir, os.fsdecode(file))}') # note the usage on the un-cleaned file name for this log entry

          # check if you can actually do anything (exceeded the usage limit)
          charAllowance = getUsage()
          expectedCharCost = getCharCountOfPDF(os.path.join(inputDir, os.fsdecode(file))) # again un-clean filename since it hasn't been renamed yet
          if charAllowance <= 0:  # no more allowance this month
            logger.error(f'Skipping file due to zero usage allowance.')
            logger.removeHandler(fileHandler) # close the log before moving on to the next file
            continue # move to next file
          elif charAllowance < expectedCharCost:
            logger.error(f'Skipping file.  Remaining allowance too low for this file.')
            logger.removeHandler(fileHandler) # close the log before moving on to the next file
            continue # move to next file

          # rename if we need the filename to be made safe/clean (if same name, exits subroutine early)
          renameToSafeFilename(inputDir, os.fsdecode(file), filename)

          # translate the document
          if os.path.exists(inputFile):
            translateDocument(inputFile, tmpFile, targetLang)
          
          # append the translated PDF to the original PDF and put in outputDir
          if os.path.exists(tmpFile):
            appendPDFs(inputFile, tmpFile, outputFile)

          # clean up the old files, make sure that inputFiles aren't re-translated at another date
          if os.path.exists(outputFile): # if we successfully created the outputFile
            deleteFile(tmpFile)
            deleteFile(inputFile)

          logger.info(f'Finished processing file.')
          time.sleep(30) # Delay for X seconds to prevent pounding on the server.
          # getUsage() # annoyingly DeepL doesn't update their usage quickly.  So, this line may get removed as worthless.
          try:
            if not (fileHandler is None):
              logger.removeHandler(fileHandler) # close the log before moving on to the next file
          except Exception as error:
            logger.debug(f"Unable to close individual log file.  (Probably never opened in the 1st place.)")
            logger.debug(f"\tIndividual Log file: {logFile}")
            logger.debug(f"\t{error}")

        logger.info(f'----------------------------------------') # added separator line to global log
        continue # move to next file
      else:
        continue
  # Delay for X minutes until checking the directory again ... and again ... and again.
  if not wasQuotaExceeded: # skip the delay if we're just going to delay for a much longer period due to exceeding the usage allowance
    sleepWithACountdown(checkPeriodSec)
      
# #####################################
# To Do
# #####################################
# * support all DeepL file-types
#   * not sure what do with files that can't be appended
      
# #####################################
# Version history
# #####################################
# v1.0 2023-03-13
#    * Basic functionality
#
# v2.0 2023-03-17
#    * added While loop
#    * improved logging, error handling, and basic functioning.
#
# v2.1.0 2023-03-18
#    * various edits made to make compatible with Docker and polish
#
# v2.1.1 2023-03-24
#    * translateDocument(): added better parsing of the error message
#    * during initial usage check now checks if you have "some" quota but not enough to actually do anything.
#    * added numbOfSecsTillRenewal()
#    * added ENV{AM_I_IN_A_DOCKER_CONTAINER} to set variables depending on the Docker state
#


