# AutoTranslate

AutoTranslate is a small lightweight application that uses the DeepL API to translate PDF documents from one language to another in a layout preserving way.

## Features

* **NEW in v2.4**: Web interface for file uploads and job monitoring
* Monitors a directory for newly added PDF files
* Translates those files via the DeepL API
* Appends the translation document and original (untranslated) document into a single file
* Places that final file in an output directory (useful for consumption by programs like Paperless-Ngx)
* Sends notifications when the file has been translated or an error occurs
* Generates log files in case of any errors occur
* If you have exceeded your DeepL API usage for the month, the program will sleep until the next month
* Authentication (API Key) via Docker ENV variables
* CLI interface or Web UI to handle one file at a time or directory monitoring

AutoTranslate doesn't cost any money. The DeepL API has a free option. However, the DeepL API has a usage limit (per month) and you can pay extra to DeepL to increase that monthly limit.

## Requirements

You **must** have a DeepL API account!

They have a free account which allows the translation of 10 documents per month. DeepL measures the quota in terms of *characters* (500,000 for the free account), but each document, regardless of size, seems to consume 50,000 characters. Assuming you only translate documents, that means you can translate 10 documents a month for free.

DeepL does have non-free accounts which let you translate millions of characters (20 documents) per month.

Please sign up for an account on [the DeepL website](https://www.deepl.com/pro-api).

## Getting your DeepL API/Authentication Key

* Signing up for an account is the first step: [the DeepL website](https://www.deepl.com/pro-api).
* Simply go to [your account summary page on the DeepL website](https://www.deepl.com/account/summary).
* Scroll down to the bottom of the page. There is the key.
* Copy this key into your Docker compose/command, as shown below.

## Getting AutoTranslate

AutoTranslate is a small Docker container. Pull the latest release from the [Docker Hub](https://hub.docker.com/r/zorbatherainy/autotranslate):

```
$ docker pull zorbatherainy/autotranslate:latest
```

If you want to run the Python script outside of Docker, just copy it from [the GitHub repository](https://github.com/zorbaTheRainy/autotranslate).

## Using AutoTranslate

### Docker

The simplest way to use AutoTranslate is to run the docker container. Starting with v2.4.0, the container includes a web server interface for easy file uploads and monitoring.

Before we get to the Docker instructions, you should make sure everything is set up.

You will need:

* The DeepL API Key (we discussed this above).
* An **input directory** that will be monitored, where you put any PDF files you wish to be translated. **NOTE**: The files will be deleted once they have been translated (to prevent re-translating them over and over again). If you wish to keep the originals (in a pure state), only put copies in the input directory. Although, as the next bullet point says, the originals will be appended to the translated version in the output directory. You will still have the native text.
* An **output directory** where the translated files will be placed. The translated & non-translated files will be appended into one file. Therefore, yo will always have the non-translated text, if you need it.
* A directory to place the **log files**. If you do not wish to have log files, you may map this to /dev/null or the Windows equivalent.

Once you have created these you are ready for the Docker command.

I use compose. Conversion of the below into the command line command is "left as an exercise for the reader".

#### Sample Docker compose

```
---
version: "2.1"

services:
	autotranslate:
		image: zorbatherainy/autotranslate
		container_name: autotranslate
		environment:
			- DEEPL_AUTH_KEY=aaa11111-a1aa-1a11-a1a1-a1a1aa11aaa1:aa              # (mandatory) get your key at https://www.deepl.com/account/summary
			# - DEEPL_TARGET_LANG=EN-US                                           # (near mandatory)  The language isn't really changeable on the fly.  Assumes you want all your files translated to a common language
			# - DEEPL_USAGE_RENEWAL_DAY=1                                         # (optional) If you put the day of the month your DeepL allowance resets, then the expired usage sleeping will be more accurate.  Otherwise, it just waits 7 days before trying again.
			# - CHECK_EVERY_X_MINUTES=15                                          # (optional) How often you want the inputDir scanned for new files
			# - ORIGINAL_BEFORE_TRANSLATION=0                                     # (optional) When appending the original and translated files, which should go first?
			# - TRANSLATE_FILENAME=1                                              # (optional) Should the filename also be translated?
			# - USE_WEB_SERVER=1                                                  # (optional) Should the web server be run?
			# - NOTIFY_URLS='mailtos://example.com:12522?user=user1@example.com&pass=pass123&smtp=mail.example.com&from=joe@example.com&to=bob@example.com'
                                        # (optional) Apprise-style comma separated list of URLs to notify when errors or the final translation occurs.

		volumes:
			- /etc/localtime:/etc/localtime:ro                                    # (optional) Sync to host time
			- /volume1/translate/:/inputDir                                       # (mandatory) The directory where you put the un-translated file
			- /volume1/consume/:/outputDir                                        # (mandatory) The directory where AutoTranslate will put the translated file
			- /volume1/autotranslate_logs/:/logDir                                # (near mandatory) The directory where log files are stored, 1 per input file and a master log file
		ports:
			- "8881:8010"                                                         # (optional) Map web interface to host port 8881
```

#### Environment variables and configuration

AutoTranslate variables and configurations are passed via environment variables.

The only **mandatory** variable is the DEEPL\_AUTH\_KEY.

| Env Variable | Default | Purpose |
| ------------ | ------- | ------- |
| DEEPL\_AUTH\_KEY | None | The Authentication Key from DeepL that allows you to use their API server. |
| DEEPL\_TARGET\_LANG | EN-US | The target language to translate the documents into. The original language will be auto-detected by DeepL. A list of language codes may be found in [the DeepL API documentation](https://www.deepl.com/docs-api/translate-text) under the `target_lang` parameter. |
| DEEPL\_USAGE\_RENEWAL\_DAY | 0 | Eventually, you are going to try to translate more documents than you have quota with DeepL. At this point, the script will stop trying to translate and sleep until your quota is renewed (the start of **your** new month). This variable tells the script what day of the month your quota will be renewed, and the program can wake-up and resume translating. For example, if your DeepL subscription renews on the 5th of the month, put "5" in this variable. If this variable is not set, the program will wake-up every 7 days and see if your quota has been renewed. Acceptable values are 1-31. Values outside that range are treated as if the variable was not set. |
| CHECK\_EVERY\_X\_MINUTES | 15 | The frequency at which the input directory will be scanned for any new files. |
| ORIGINAL\_BEFORE\_TRANSLATION | false | (v2.1.3+) When appending the original and translated files, which should go first? |
| TRANSLATE\_FILENAME | true | (v2.2.0+) Should the filename also be translated? This is a bit experimental. |
| NOTIFY\_URLS | "" | (v2.3.0+) A comma separated list of [Apprise formatted URLs](https://github.com/caronc/apprise/wiki). |
| INPUT\_DIR | "/inputDir/" in a container<br>"./input/" outside a container | (v2.3.0+) The directory where you put the un-translated PDF files. Non-PDF files will be ignored. |
| OUTPUT\_DIR | "/outputDir/" in a container<br>"./output/" outside a container | (v2.3.0+) The directory where AutoTranslate will put the translated PDF files, which have been appended to the original un-translated file. |
| LOG\_DIR | "/logDir/" in a container<br>"./logs/" outside a container | (v2.3.0+) The directory where log files are stored, 1 per input file and a master log file (`_autotranslate.log`). |
| AT\_BASE\_DIR | "." | (v2.4.0+) Base directory for input/output/logs when running **outside** containers. Used as prefix for relative paths. For example, `LOG_DIR` is really `${AT_BASE_DIR}/logs/` |
| USE\_WEB\_SERVER | false | (v2.4.0+) Flag to turn on the web server. |

#### Volumes

| Volume | Purpose |
| ------ | ------- |
| inputDir | The directory where you put the un-translated PDF files. Non-PDF files will be ignored. |
| outputDir | The directory where AutoTranslate will put the translated PDF files, which have been appended to the original un-translated file. |
| logDir | The directory where log files are stored, 1 per input file and a master log file (`_autotranslate.log`). |

### Command Line

Autotranslate may be used from the command line in one of two modes: single file, or directory monitor.

**Directory monitor mode** is what is used in the Docker, and Autotranslate.py simply monitors a directory for PDFs, translates them, and provides the translated PDFs.

**Single file mode** is what is used when you want to translate one PDF file. You provide the filename as input and it will translate it and put it in the output directory.

#### Usage

```
usage: autotranslate.py [-h] [-i INPUT_DIR] [-o OUTPUT_DIR] [-l LOG_DIR] [-k AUTH_KEY] [-s SERVER_URL] [-t TARGET_LANG] [-c CHECK_EVERY_X_MINUTES] [-r RENEWAL_DAY] [-B] [-N] [-u NOTIFY_URLS] [--version]
						[file]
```

##### Examples

Translate a single file, and put the output in the `consume` directory:

``` bash
autotranslate.py input.pdf -o ./consume/ -k YOUR_API_KEY
```

Run in directory monitor mode, and put the logs in the `~/logs` directory, but use the default input and output directories otherwise:

``` bash
autotranslate.py -l ~/logs -k YOUR_API_KEY
```

Run in directory monitor mode, with th defaults, but with 2 Apprise notification services (gmail and discord)

``` bash
autotranslate.py --k YOUR_API_KEY --notify-urls 'mailto://boo:HisAppPassword@gmail.com,discord://4174216298/JHMHI8qBe7bk2ZwO5U711o3dV_js'
```

#### Command Line Arguments (v2.3.0+)

| Short | Long | Overrides ENV |
| ----- | ---- | ------------- |
| `-i` | `--input-dir` | INPUT\_DIR |
| `-o` | `--output-dir` | OUTPUT\_DIR |
| `-l` | `--log-dir` | LOG\_DIR |
| `-k` | `--auth-key` or `--api-key` | DEEPL\_AUTH\_KEY |
| `-t` | `--target-lang` | DEEPL\_TARGET\_LANG |
| `-c` | `--check-every-x-minutes` | CHECK\_EVERY\_X\_MINUTES |
| `-r` | `--renewal-day` | DEEPL\_USAGE\_RENEWAL\_DAY |
| `-B` | `--original-before-translation` | ORIGINAL\_BEFORE\_TRANSLATION |
| `-N` | `--translate-filename` | TRANSLATE\_FILENAME |
| `-u` | `--notify-urls` | NOTIFY\_URLS |
| `-w` | `--web-server` | USE\_WEB\_SERVER |
<br>
| Short | Long | Meaning |
| ----- | ---- | ------- |
| `-h` | `--help` | Prints out the standard "how to use" message |
|  | `--version` | Prints out the version number |
|  | `[file]` | The file you wish to translate. Puts Autotranslate.py into single-file mode. |

In general, CLI switches override ENV variables.

Using `--original-before-translation` and `--translate-filename` are the same as setting their ENVs to `1` or `True`.

`--notify-urls` and the ENV `NOTIFY_URLS` are actually merged. So, the resultant URL list will be the combination of both values. However, invalid Apprise URLs are discarded.

(v2.4.0+)  If you have a `.env` file in the same folder as you run `autotranslate.py`, those ENV values will be automatically loaded.  (This was mostly useful for testing.)

### Web Interface (v2.4.0+)

AutoTranslate now includes a built-in web server that provides a user-friendly interface for uploading and translating PDF files.

Note: Translated files are output **both** to the web browser **and** the output directory (as per the pre-2.4.0 behavior).

#### Starting the Web Server

**Docker:**
The Docker container does NOT run the web server autoamtically. You must set the USE\_WEB\_SERVER environment variable to `true` to use the web server.The Docker container continues to automatically run in directory monitoring mode, however.

Running the container with the `USE_WEB_SERVER` environment variable set to `true` will start BOTH the web server and directory monitoring mode, simultaniously.

Access it at `http://localhost:8881` (or your mapped port).

**URL:**
The web interface will be available at `http://localhost:8010`.
The normal script behaviour, via the input/output directories, is still available.

#### Web Features

* **File Upload**: Drag-and-drop PDF files or browse to select them
* **Per-File Configuration Options**: Select target language, enable filename translation, and choose whether to include the original document
* **Global Configuration via ENV**: The API Key and other configuration options can be set via the environment variables, as before.
* **Real-time Monitoring**: View translation progress and job status
* **Log Access**: View service logs, web logs, and job-specific logs
* **Job History**: Access completed translations and their results (good onl until reboot, or some other programs - like Paperless - moves the output file)

Figure 1: Initial state of the web interface showing the upload form and options.
<img src="https://raw.githubusercontent.com/zorbaTheRainy/main/documentation/images/web_ui_initial.png" alt="Web UI Initial State" style="width: 80%; height: auto;">

Figure 2: Web interface during an active translation showing log/progress monitoring.
<img src="https://raw.githubusercontent.com/zorbaTheRainy/main/documentation/images/web_ui_translation.png" alt="Web UI During Translation" style="width: 80%; height: auto;">

#### Web Server Configuration

The web server runs on port 8010 by default. You can change this via Docker port mapping.

### Notifications (v2.3.0+)

Autotranslate can now send you notifications when things occur, specifically errors or your file being translated.
The translated file, in addition to being put in the output directory, will now also be sent to you via a notification (if your service supports file attachments, e.g., email).
Any error messages (including Docker stops) will be sent to you via a notification. No more not knowing things have failed silently.

Autotranslate handles notifications via the [Apprise Python library](https://github.com/caronc/apprise), which allows Autotranslate to support 100+ notification systems out-of-the-box!

It will now support email, Discord, Gotify, Ntfy, ITTT, and so many more. But, ... you have to provide it with a Apprise-style URL (or multiple) in order for it to work (via the ENV `NOTIFY_URLS` or CLI switch `--notify-urls`).
Please look at [Apprise's documentation](https://github.com/caronc/apprise/wiki) to figure out how to format your desired URLs.
You can see a complex email example in the Docker compose example above.

Multiple services or URLs can be used by putting a comma after each one. Again, see the example above.

I **strongly** suggest trying out the desired `NOTIFY_URLS` in either single-file mode or by looking at the container log for Docker, as the error messages for registering the URLs occur before the global file log is fully setup. (If you're worried about using your DeepL quota, put in a fake API key, and the URLs will be processed but any translation attempts will fail due to an invalid key. Plus you'll then get a notification that your translation failed.)

### Other random quirks you may be interested in

* **Filename Cleaning**: Many filenames in non-English languages will not upload well to the API server. The script will make an attempt to clean-up the filename, removing any troublesome/Unicode characters, and replacing them with ASCII characters instead. Whitespace is also replaced with underscores. If the filename is not translated, the output filename will be this cleaned filename.
* **Filename Translation, the quota**: In order to not use up the DeepL quota, I used a separate Python library to perform filename translation and language detection. I could use the DeepL API but each document uses a fixed 50,000 characters and if I use non-document side of the API to translate 30 characters of a filename, you are down from 10 free documents a month to 9 (500,000 - 30 = 499,970, and 499,970/50,000 is 9.9, which is less that 10. Meaning that 10th document will exceed the quota and DeepL won't translate it.). This feature will work until the Python library breaks. But the DeepL API document translation should still work. You just won't get the nice new filename.
* **Filename Translation, the translation**: In order to not use up the DeepL quota, I used a separate Python library to perform filename translation and language detection. The Python library that does the translation attempts to do it via the standard web page interface, and I wouldn't be surprised if this breaks someday. Right now I try Google.
* **Mock DeepL Server**: If you wish to test how your code functions with the DeepL API, there is a fake/mock server at [DeepLcom/deepl-mock on GitHub](https://github.com/DeepLcom/deepl-mock). It is very limited, but it lets you test your code (to an extent) without running through your actual DeepL quota. A pre-built docker image may be found at [thibauddemay/deepl-mock](https://hub.docker.com/r/thibauddemay/deepl-mock). This was a useful feature in the pre-v2.0.0 version of the script, but it is now deprecated.
If you wish to use a mock DeepL server use the ENV variable DEEPL\_SERVER\_URL or `-s`, `--server-url`. In the Docker compose you would include the following line (adjust "http://localhost:3000" to point to your deepl-mock container.)

```
  environment:
  	- DEEPL_SERVER_URL=http://localhost:3000                            # (optional) "" (i.e., an empty string) is the actual DeepL server, anything else is for testing
```

## Thoughts on use with Paperless

My original idea for this project was I was tired of feeding in documents one at a time to the Google Translation web site. The Google API costs money; the DeepL API does not. I decided to use the DeepL API.

At about the same time, I started to look into [Paperless](https://github.com/paperless-ngx/paperless-ngx), "a document management system that transforms your physical documents into a searchable online archive so you can keep, well, less paper."

AutoTranslate works really well with Paperless.

### Auto-Tagging and Consumption by Paperless

Like AutoTranslate, Paperless works on a system where there is a monitored directory (named "consume") and files placed in that directory are moved to the Paperless database. Chaining the AutoTranlate output directory to the Paperless input/consume directory works very well.

To turbo-charge this, make use of [Paperless's auto-tagging feature](https://docs.paperless-ngx.com/configuration/#consume_config). Paperless can be setup to have sub-directories in the input/consume directory and to automatically tag any files in there with the sub-directory's name. By placing the output of AutoTranslate into a Paperless sub-directory, you can tag (in Paperless) those files with the original language or another tag.

For example, a directory named:

```
/paperless_data/consume/german
```

would result in Paperless importing files (in `/consume/german`) tagged with "german". This allows you to separate those from non-translated files in Paperless.

AutoTranslate doesn't use separate output directories for each source language, but I am assuming you really only translate from one language (probably the foreign country you live in). If you have multiple languages you could run multiple instances of the docker, each pointing to a different sub-directory.

To make this work, you need to set some Paperless ENV variables (via Paperless's docker compose file). Specifically:

```
PAPERLESS_CONSUMER_RECURSIVE: 1
PAPERLESS_CONSUMER_SUBDIRS_AS_TAGS: 1
```

Again, look at [Paperless's consume config documentation](https://docs.paperless-ngx.com/configuration/#consume_config).

### Pre-Consumption Script

You could use the autotranslate.py Python script directly as a Paperless Pre-Consumption script. The documentation for [the Pre-Consumption script function is here](https://docs.paperless-ngx.com/advanced_usage/#pre-consume-script).

The reason I don't recommend this is because it would run all files imported/consumed into Paperless through the DeepL API, eating up you monthly quota. But it is possible.

## FAQs

### Is it Abandoned?

If this project has not been updated in awhile everyone asks, "Is it abandoned?"

No, probably not.

It is probably just **done**.

When I get this to a state where it doesn't need improvement, I won't improve it. I am not making improvements just to show "progress". I use this too much to just ignore it. If it works and I am happy, I won't change it. If it breaks and I need it, I'll fix it.

But if it works well enough, I'll do something else (the urge to tinker aside).

### Can you use something besides DeepL?

In theory: Sure.

In fact: No.

Here is the problem (and I am open to solutions): I want a PDF translator that does layout‑preserving PDF translation. That means it replaces Language-A text with Language-B text on the exact same place shown on the PDF. The goal is to have a properly formatted PDF that is readable by Language-B speakers where all the context of placement and images are preserved.

There are plenty of text-to-text or PDF-to-text solutions out there. But that isn't what I want.

I haven't found a free, OSS solution that does that.
LibreTranslate does not support PDF, and doesn't do layout‑preserving PDF translation.

And it needs to work via an API (not web form based)

There are a few APIs (Google, BeringAI, etc.), but they all cost money. I don't want to pay for a service that I don't use.

## License

[MIT](LICENSE)