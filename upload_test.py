#!/usr/bin/env python3
import requests

# Hardcoded config
UPLOAD_URL = "https://baltech-industry.com/upload.php"
FILE_PATH = "dummy123.png"   # make sure dummy.png exists in same folder
FIELD_NAME = "file"

def main():
    with open(FILE_PATH, "rb") as f:
        files = {FIELD_NAME: (FILE_PATH, f, "image/png")}
        resp = requests.post(UPLOAD_URL, files=files, timeout=30)
    print("Status:", resp.status_code)
    print("Body:", resp.text[:500])

if __name__ == "__main__":
    main()
