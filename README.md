# AutoTranslate

AutoTranslate is a small lightweight application that uses the DeepL API to translate PDF documents from one language to another.

## Features

- Monitors a directory for newly added PDF files
- Translates those files via the DeepL API
- Appends the translation document and original (untranslated) document into a single file
- Places that final file in an output directory (useful for consumption by programs like Paperless-Ngx)
- Generates log files in case of any errors occur
- If you have exceeded your DeepL API usage for the month, the program will sleep until the next month
- Authentication (API Key) via Docker ENV variables
- No web interface

AutoTranslate doesn't cost any money.  The DeepL API has a free option.  However, the DeepL API has a usage limit (per month) and you can pay extra to DeepL to increase that monthly limit.

## Requirements

You **must** have a DeepL API account!

They have a free account which allows the translation of 10 documents per month.  DeepL measures the quota in terms of *characters* (500,000 for the free account), but each document, regardless of size, seems to consume 50,000 characters.  Assuming you only translate documents, that means you can translate 10 documents a month for free.  

DeepL does have non-free accounts which let you translate millions of characters (20 documents) per month.  

Please sign up for an account on [the DeepL website](https://www.deepl.com/pro-api).


## Getting your DeepL API/Authentication  Key

- Simply go to [your account summary page on the DeepL website](https://www.deepl.com/account/summary).
- Scroll down to the bottom of the page.  There is the key.
- Copy this key into your Docker compose/command, as shown below.

## Getting AutoTranslate

AutoTranslate is a small Docker container. Pull the latest release from the [Docker Hub](https://hub.docker.com/r/zorbatherainy/autotranslate):

    $ docker pull zorbatherainy/autotranslate:latest

If you want to run the Python script outside of Docker, just copy it from [the GitHub repository](https://github.com/zorbaTheRainy/autotranslate).

## Using AutoTranslate

The simplest way to use AutoTranslate is to run the docker container. 

Before we get to the Docker instructions, you should make sure everything is set up.

You will need:
- The DeepL API Key (we discussed this above).
- An **input directory** that will be monitored, where you put any PDF files you wish to be translated.  **NOTE**: The files will be deleted once they have been translated (to prevent re-translating them over and over again).  If you wish to keep the originals (in a pure state), only put copies in the input directory.  Although, as the next bullet point says, the originals will be appended to the translated version in the output directory.  You will still have the native text.
- An **output directory** where the translated files will be placed.  The translated & non-translated files will be appended into one file.  Therefore, yo will always have the non-translated text, if you need it.
- A directory to place the **log files**.  If you do not wish to have log files, you may map this to /dev/null or the Windows equivalent.

Once you have created these you are ready for the Docker command.

I use compose.  Conversion of the below into the command line command is "left as an exercise for the reader".

### Sample Docker compose

	---
	version: "2.1"

	services:
		autotranslate:
			image: zorbatherainy/autotranslate
			container_name: autotranslate
			environment:
				- DEEPL_AUTH_KEY=aaa11111-a1aa-1a11-a1a1-a1a1aa11aaa1:aa              # (mandatory) get your key at https://www.deepl.com/account/summary
				# - DEEPL_SERVER_URL=http://localhost:3000                            # (optional) "" (i.e., an empty string) is the actual DeepL server, anything else is for testing 
				# - DEEPL_USAGE_RENEWAL_DAY=1                                         # (optional) If you put the day of the month your DeepL allowance resets, then the expired usage sleeping will be more accurate.  Otherwise, it just waits 7 days before trying again.
				# - DEEPL_TARGET_LANG=EN-US                                           # (near mandatory)  The language isn't really changeable on the fly.  Assumes you want all your files translated to a common language
				# - CHECK_EVERY_X_MINUTES=15                                          # (optional) How often you want the inputDir scanned for new files
				# - ORIGINAL_BEFORE_TRANSLATION=0                                     # (optional) When appending the original and translated files, which should go first?
				# - TRANSLATE_FILENAME=1                                              # (optional) Should the filename also be translated?
			volumes:
				- /etc/localtime:/etc/localtime:ro                                    # (optional) Sync to host time
				- /volume1/translate/:/inputDir                                       # (mandatory) The directory where you put the un-translated file
				- /volume1/consume/:/outputDir                                        # (mandatory) The directory where AutoTranslate will put the translated file
				- /volume1/autotranslate_logs/:/logDir                                # (near mandatory) The directory where log files are stored, 1 per input file and a master log file



#### Environment variables and configuration

AutoTranslate variables and configurations are passed via environment variables.

The only **mandatory** variable is the DEEPL_AUTH_KEY.

| Env Variable           | Default | Purpose             |
| ---------------------- | ------- | ------- |
| DEEPL_AUTH_KEY         | None    | The Authentication Key from DeepL that allows you to use their API server. |
| DEEPL_SERVER_URL        | ""    | If you wish to use a fake DeepL server for testing, put in the URL here.  This it totally unnecessary for production use. |
| DEEPL_USAGE_RENEWAL_DAY | 0    | Eventually, you are going to try to translate more documents than you have quota with DeepL.  At this point, the script will stop trying to translate and sleep until your quota is renewed (the start of your new month).  This variable tells the script what day of the month your quota will be renewed, and the program can wake-up and resume translating.  For example, if your DeepL subscription renews on the 5th of the month, put "5" in this variable.  If this variable is not set, the program will wake-up every 7 days and see if your quota has been renewed.  Acceptable values are 1-31.  Values outside that range are treated as if the variable was not set. |
| DEEPL_TARGET_LANG       | EN-US    | The target language to translate the documents into.  The original language will be auto-detected by DeepL.  A list of language codes may be found in [the DeepL API documentation](https://www.deepl.com/docs-api/translate-text) under the `target_lang` parameter. |
| CHECK_EVERY_X_MINUTES   | 15    | The frequency at which the input directory will be scanned for any new files. |
| ORIGINAL_BEFORE_TRANSLATION | false    | (> v2.1.3) When appending the original and translated files, which should go first? |
| TRANSLATE_FILENAME      | true    | (> v2.2.0) Should the filename also be translated?  This is a bit experimental. |

#### Volumes
| Volume           | Purpose             |
| ---------------------- | ------- |
| inputDir | The directory where you put the un-translated PDF files.  Non-PDF files will be ignored. |
| outputDir | The directory where AutoTranslate will put the translated PDF files, which have been appended to the original un-translated file. |
| logDir | The directory where log files are stored, 1 per input file and a master log file (`_autotranslate.log`). |


#### Other random quirks you may be interested in

- **Filename cleaning**: Many filenames in non-English languages will not upload well to the API server.  The script will make an attempt to clean-up the filename, removing any troublesome/Unicode characters, and replacing them with ASCII characters instead.  Whitespace is also replaced with underscores.  If the filename is not translated, the output filename will be this cleaned filename.
- **Filename Translation, the quota**:  In order to not use up the DeepL quota, I used a separate Python library to perform filename translation and language detection.  I could use the DeepL API but each document uses a fixed 50,000 characters and if I use non-document side of the API to translate 30 characters of a filename, you are down from 10 free documents a month to 9 (500,000 - 30 = 499,970, and 499,970/50,000 is 9.9, which is less that 10.  Meaning that 10th document will exceed the quota and DeepL won't translate it.).  This feature will work until the Python library breaks.  But the DeepL API document translation should still work.  You just won't get the nice new filename.
- **Filename Translation, the translation**:  In order to not use up the DeepL quota, I used a separate Python library to perform filename translation and language detection.  The Python library that does the translation attempts to do it via the standard web page interface, and I wouldn't be surprised if this breaks someday.  Right now I try three web pages (Bing, Google, and DeepL non-API), in that order, until one of them works.   
- **Filename Translation, language reporting**:  In order to not use up the DeepL quota, I used a separate Python library to perform filename translation and language detection.  The language detection reported by this Python library is not as good as the DeepL API, but is only used for reporting to the log files.  If it is wrong, don't freak out.  DeepL does its own detection on the whole document.  It is just that the Python library isn't very good for something as short as a filename.
- **Mock DeepL Server**:  If you wish to test out the DeepL API, there is a fake/mock server at [DeepLcom/deepl-mock on GitHub](https://github.com/DeepLcom/deepl-mock).  It is very limited, but it lets you test your code (to an extent) without running through your actual DeepL quota.  That is what the ENV variable DEEPL_SERVER_URL is for.  A pre-built docker image may be found at [thibauddemay/deepl-mock](https://hub.docker.com/r/thibauddemay/deepl-mock).


## Thoughts on use with Paperless

My original idea for this was I was tired of feeding in documents one at a time to the Google Translation web site.

At about the same time, I started to look into [Paperless](https://github.com/paperless-ngx/paperless-ngx), "a document management system that transforms your physical documents into a searchable online archive so you can keep, well, less paper."  

AutoTranslate works really well with Paperless.

### Auto-Tagging and Consumption by Paperless

Like AutoTranslate, Paperless works on a system where there is a monitored directory (named "consume") and files placed in that directory are moved to the Paperless database.  Chaining the AutoTranlate output directory to the Paperless input/consume directory works very well.

To turbo-charge this, make use of [Paperless's auto-tagging feature](https://docs.paperless-ngx.com/configuration/#consume_config).  Paperless can be setup to have sub-directories in the input/consume directory and to automatically tag any files in there with the sub-directory's name.  By placing the output of AutoTranslate into a Paperless sub-directory, you can tag (in Paperless) those files with the original language or another tag.

For example, a directory named:

	\paperless_data\consume\german

would result in Paperless importing files (in `\consume\german`) tagged with "german".  This allows you to separate those from non-translated files in Paperless.


AutoTranslate doesn't use separate output directories for each source language, but I am assuming you really only translate from one language (probably the foreign country you live in).  If you have multiple languages you could run multiple instances of the docker, each pointing to a different sub-directory.

To make this work, you need to set some Paperless ENV variables (via Paperless's docker compose file).  Specifically:

	PAPERLESS_CONSUMER_RECURSIVE: 1
	PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS: 1

Again, look at [Paperless's consume config documentation](https://docs.paperless-ngx.com/configuration/#consume_config).

### Pre-Consumption Script

You could use the autotranslate.py Python script directly as a Paperless Pre-Consumption script.  The documentation for [the Pre-Consumption script function is here](https://docs.paperless-ngx.com/advanced_usage/#pre-consume-script).

The reason I don't recommend this is because it would run all files imported/consumed into Paperless through the DeepL API, eating up you monthly quota.  But it is possible.



### License

[MIT](LICENSE)

#
