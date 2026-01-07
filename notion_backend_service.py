"""
notion_backend_service.py
=========================

This module implements a small FastAPI service that receives data and a photo
from an iOS Shortcut (or any HTTP client) and creates a page in a Notion
database with the received metadata.  It demonstrates how to use Notion’s
**direct file upload** API to store images directly in Notion-managed
storage rather than relying on external hosting or temporary iCloud links.

The upload workflow follows the sequence described in Notion’s documentation:

1. **Create a file upload object** using the `POST /v1/file_uploads` endpoint.
   This returns a unique `id` and a special `upload_url`.  According to the
   docs, you must first create a file upload object before sending any data
   for security reasons【634170527858348†L88-L100】.
2. **Send the binary file contents** to Notion using the
   `POST /v1/file_uploads/{file_upload_id}/send` endpoint.  The request
   must be multipart/form‑data; you should not set a `Content-Type` header
   manually because the HTTP library will calculate the boundary for you
  【634170527858348†L160-L170】.  After this step the file’s status becomes
   `uploaded`.
3. **Attach the uploaded file** to a page or block.  Once the file status
   is `uploaded`, you can attach it to a page either via a database
   property of type `files` or as a block child【634170527858348†L278-L295】.

Environment variables
---------------------

This service reads several credentials from environment variables so you
never hard‑code secrets into your repository:

* ``NOTION_TOKEN`` – your Notion integration’s **Internal Integration Token**.
* ``NOTION_DATABASE_ID`` – the ID of the database where new pages will be
  created.  You can obtain this from the URL of your database (32
  characters before the `?v=` query string).
* ``NOTION_VERSION`` – optional.  If unset, the default version
  ``2022-06-28`` will be used.  You can upgrade to a newer version by
  setting this variable.

Deploying on Railway
--------------------

1. Create a new **Python** project on Railway and add this file to your
   repository.
2. In your Railway dashboard, configure the environment variables listed
   above with your own values.
3. Railway automatically installs dependencies from a ``requirements.txt``
   file.  Make sure to include ``fastapi`` and ``uvicorn[standard]`` along
   with ``python‑multipart`` and ``requests`` in your requirements.
4. Set the command for the Railway service to something like::

       uvicorn notion_backend_service:app --host 0.0.0.0 --port 8000

   Railway will expose the service at a public URL.

Once deployed, your iOS Shortcut can send a ``POST`` request to
``<your‑railway‑subdomain>.railway.app/upload-photo`` with the form
fields described below.
"""

import os
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
import requests

app = FastAPI(title="Notion Upload Service")

# Read environment variables for credentials.  Do not hard‑code your
# integration token or database ID; set them in the Railway environment.
NOTION_TOKEN: str | None = os.getenv("NOTION_TOKEN")
DATABASE_ID: str | None = os.getenv("NOTION_DATABASE_ID")
# Use a default Notion version if one isn't supplied.  You can set
# NOTION_VERSION in Railway if you want to upgrade to a newer API version.
NOTION_VERSION: str = os.getenv("NOTION_VERSION", "2022-06-28")

if not NOTION_TOKEN:
    raise RuntimeError(
        "NOTION_TOKEN environment variable must be set.  In Railway, add it "
        "under Project > Variables.  It should contain your Notion integration "
        "token."
    )
if not DATABASE_ID:
    raise RuntimeError(
        "NOTION_DATABASE_ID environment variable must be set.  This is the "
        "32‑character ID from your database URL."
    )


def notion_headers(additional: dict | None = None) -> dict:
    """Construct the base headers for requests to the Notion API.

    Always include the Authorization and Notion‑Version headers.  The
    Content‑Type header will be set by the caller when appropriate.
    """
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
    }
    if additional:
        headers.update(additional)
    return headers


@app.post("/upload-photo")
async def upload_photo(
    # The uploaded image.  Accept all common image formats (JPEG, PNG, etc.).
    file: UploadFile = File(..., description="Image file to upload"),
    # Metadata fields for the Notion page.
    name: str = Form(..., description="Title of the page (Name property)"),
    date: str = Form(..., description="ISO 8601 date string for the Date property"),
    context: str = Form("", description="Context field (rich text)"),
    hunger: int = Form(..., description="Hunger level (number)"),
    energy: int = Form(..., description="Energy level (number)"),
    emotion: str = Form("", description="Emotional state (text)")
) -> dict:
    """Create a page in a Notion database and upload a photo.

    The client should send a multipart/form‑data request containing the image
    under the ``file`` key and the other fields as form fields.  For example,
    from an iOS Shortcut you can use the **Get Contents of URL** action with
    Method set to POST and Request Body set to Form.  See the README for
    details.

    Returns the ID of the created page.
    """
    # 1️⃣ Create the page first, without attaching the file.  This call
    # defines the values of the database properties.  If you need to add
    # additional properties, modify this payload accordingly.
    create_payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name": {
                "title": [
                    {
                        "text": {
                            "content": name
                        }
                    }
                ]
            },
            "Date": {
                "date": {
                    "start": date
                }
            },
            "Context": {
                "rich_text": [
                    {
                        "text": {
                            "content": context
                        }
                    }
                ]
            },
            "Hunger_level": {
                "number": hunger
            },
            "Energy_level": {
                "number": energy
            },
            "Emotional_state": {
                "rich_text": [
                    {
                        "text": {
                            "content": emotion
                        }
                    }
                ]
            },
        },
    }
    page_response = requests.post(
        "https://api.notion.com/v1/pages",
        headers=notion_headers({"Content-Type": "application/json"}),
        json=create_payload,
        timeout=15,
    )
    if page_response.status_code // 100 != 2:
        raise HTTPException(
            status_code=page_response.status_code,
            detail=f"Failed to create Notion page: {page_response.text}",
        )
    page_id = page_response.json().get("id")

    # 2️⃣ Create a file upload object.  According to Notion's documentation,
    # you must first call POST /v1/file_uploads to obtain an upload URL and ID
    #【634170527858348†L88-L100】.
    upload_meta_payload = {
        "file_name": file.filename,
        "content_type": file.content_type or "application/octet-stream",
    }
    upload_meta_response = requests.post(
        "https://api.notion.com/v1/file_uploads",
        headers=notion_headers({"Content-Type": "application/json"}),
        json=upload_meta_payload,
        timeout=15,
    )
    if upload_meta_response.status_code // 100 != 2:
        raise HTTPException(
            status_code=upload_meta_response.status_code,
            detail=f"Failed to create file upload object: {upload_meta_response.text}",
        )
    upload_info = upload_meta_response.json()
    file_upload_id = upload_info["id"]
    upload_url = upload_info["upload_url"]

    # 3️⃣ Send the binary file contents to Notion.  The /send endpoint
    # requires a multipart/form‑data POST.  We let the requests library set
    # Content-Type and boundary automatically【634170527858348†L160-L170】.
    file_bytes = await file.read()
    send_response = requests.post(
        upload_url,
        headers=notion_headers(),  # Only auth/version; requests sets Content-Type
        files={
            "file": (file.filename, file_bytes, file.content_type or "application/octet-stream")
        },
        timeout=30,
    )
    if send_response.status_code // 100 != 2:
        raise HTTPException(
            status_code=send_response.status_code,
            detail=f"Failed to upload file contents: {send_response.text}",
        )

    # 4️⃣ Attach the uploaded file to the newly created page.  Once the file
    # status is 'uploaded', you can include it as a file_upload object in a
    # block or property【634170527858348†L278-L295】.  Here we append an image
    # block to the page so the picture appears in the page content.
    attach_payload = {
        "children": [
            {
                "object": "block",
                "type": "image",
                "image": {
                    "type": "file_upload",
                    "file_upload": {
                        "id": file_upload_id
                    }
                }
            }
        ]
    }
    attach_response = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=notion_headers({"Content-Type": "application/json"}),
        json=attach_payload,
        timeout=15,
    )
    if attach_response.status_code // 100 != 2:
        raise HTTPException(
            status_code=attach_response.status_code,
            detail=f"Failed to attach file to page: {attach_response.text}",
        )

    # You can alternatively attach the file to a Files property instead of
    # creating an image block.  To do so, send a PATCH to
    # `/v1/pages/{page_id}` with a property value like:
    #
    #     {
    #         "Photo": {
    #             "files": [
    #                 {
    #                     "type": "file_upload",
    #                     "file_upload": {"id": file_upload_id}
    #                 }
    #             ]
    #         }
    #     }
    #
    # This service uses an image block for better visualisation, but you
    # can adapt it easily.

    return {"page_id": page_id}
