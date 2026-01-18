# Release Notes for AutoTranslate 2.4.0 - 2025-12-27

## Overview

Oh, we have a web-interface now!!!!

AutoTranslate 2.4.0 introduces a web-based interface alongside the existing CLI functionality. This version adds a web server for file uploads and log monitoring.

## New Features

### Web Server Interface

* Added web interface with Flask framework
* Drag-and-drop PDF file uploads with progress (log) reporting
* Log viewing in the browser with auto-refresh capabilities
* Directory monitoring from the previous versions continues
* Translated files now output to **both** the web interface (if input that way) **and** the output directory (for Paperless integration)

### Enhanced Configuration

* Per-translated-file configuration support via the web page (API key remains universal)
* `USE_WEB_SERVER` environment variable added to turn on the web server
* `AT_BASE_DIR` environment variable added for easier non-Docker usage
* Environment file support (.env) using python-dotenv for easier testing and general usage

### Backstage Improvements

* API key is now obscured when outputting it to the log file
* Threading support for concurrent web and directory operations, which resulted in a lot of changes

## Migration Guide

1. **CLI users**: No changes required; existing functionality is preserved
2. **Docker compose**: 
    * Add `USE_WEB_SERVER=1` to you ENV variables to turn on the web server.
    * Include port mapping (e.g., `ports:`) for web access
3. **Web users**: Access the interface at `http://localhost:8010` (adjust port mapping as needed)

## Dependencies

New dependencies added:

* autotranslate_web_server.py
  * `Flask==3.1.2`
  * `ansi2html==1.9.2`
* autotranslate.py
  * `dotenv==0.9.9`
