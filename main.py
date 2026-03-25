import os
import re
import json
import base64
import uuid
import logging
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel
from playwright.sync_api import sync_playwright
import gspread
from google.oauth2.service_account import Credentials

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
log = logging.getLogger(__name__)

app = FastAPI()

TARGET_RPC = "Zj93ge"

COURSES = {
    "B. Pugh ENG 2D1-3": "https://classroom.google.com/u/0/w/ODQyNDU5NzA3NDg5/t/all",
    "P2 Gr. 10 Science": "https://classroom.google.com/u/0/w/ODI1MDk5NzUwNzAz/t/all",
    "Grade 10 Canadian History 2026": "https://classroom.google.com/u/0/w/ODI0OTk1ODE1MjA4/t/all",
    "ICD2O1 - Gr. 10 Digital Technologies and Innovations in the Changing World": "https://classroom.google.com/u/0/w/NTI0MDY0Njk5MjQw/t/all",
}

jobs = {}


class StartJobRequest(BaseModel):
    email: str
    password: str
    sheetLink: str


class StartJobResponse(BaseModel):
    jobId: str


class JobStatusResponse(BaseModel):
    status: str
    progress: Optional[float] = None
    message: Optional[str] = None


def create_job() -> str:
    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "status": "queued",
        "progress": 0.0,
        "message": "Queued"
    }
    return job_id


def update_job(job_id: str, *, status=None, progress=None, message=None):
    if job_id not in jobs:
        return
    if status is not None:
        jobs[job_id]["status"] = status
    if progress is not None:
        jobs[job_id]["progress"] = progress
    if message is not None:
        jobs[job_id]["message"] = message


def extract_sheet_id(sheet_link: str) -> str:
    match = re.search(r"/d/([a-zA-Z0-9-_]+)", sheet_link)
    if not match:
        raise ValueError("Invalid Google Sheet link")
    return match.group(1)


def extract_student_number(email: str) -> str:
    return email.split("@")[0].strip()


def get_sheet(sheet_id: str):
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds_json_raw = os.getenv("GOOGLE_CREDS_JSON")
    if not creds_json_raw:
        raise Exception("Missing GOOGLE_CREDS_JSON environment variable")

    info = json.loads(creds_json_raw)
    creds = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id).sheet1


def parse_due_ms(ms):
    try:
        if ms is None:
            return ""
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def sort_due(row):
    try:
        return datetime.strptime(row["due"], "%Y-%m-%d %H:%M")
    except Exception:
        return datetime.max


def to_classroom_encoded_id(raw_id: str) -> str:
    raw = str(raw_id).encode("utf-8")
    return base64.b64encode(raw).decode("utf-8").rstrip("=")


def extract_wrb_entries(text: str):
    entries = []
    text = re.sub(r"^\)\]\}'\s*", "", text)

    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("["):
            continue

        try:
            outer = json.loads(line)
        except Exception:
            continue

        if not isinstance(outer, list):
            continue

        for item in outer:
            try:
                if (
                    isinstance(item, list)
                    and len(item) >= 3
                    and item[0] == "wrb.fr"
                    and isinstance(item[1], str)
                    and isinstance(item[2], str)
                ):
                    entries.append({
                        "rpc_id": item[1],
                        "inner": json.loads(item[2]),
                    })
            except Exception:
                continue

    return entries


def parse_zj93ge_submissions(inner, course_name):
    assignments = []

    try:
        entry_list = None
        for item in inner:
            if isinstance(item, list) and item and isinstance(item[0], list):
                entry_list = item
                break

        if not entry_list:
            return []

        for entry in entry_list:
            try:
                assignment_id = str(entry[0][1][0])

                course_id = ""
                try:
                    course_id = str(entry[0][1][1][0])
                except Exception:
                    pass

                due_ms = entry[2] if len(entry) > 2 else None
                status_code = entry[5] if len(entry) > 5 else None

                if status_code == 2:
                    status = "Submitted"
                elif status_code == 1:
                    status = "Assigned"
                else:
                    status = "Not Submitted"

                assignments.append({
                    "id": assignment_id,
                    "course_id": course_id,
                    "title": "",
                    "description": "",
                    "attachments": "",
                    "due": parse_due_ms(due_ms),
                    "course": course_name,
                    "status": status,
                })

            except Exception as e:
                log.debug(f"Skipping Zj93ge entry: {e}")

    except Exception as e:
        log.error(f"parse_zj93ge_submissions error: {e}")

    return assignments


def scrape_assignment_detail(page, course_id_raw, assignment_id_raw):
    course_id = to_classroom_encoded_id(course_id_raw)
    assignment_id = to_classroom_encoded_id(assignment_id_raw)

    url = f"https://classroom.google.com/u/0/c/{course_id}/a/{assignment_id}/details"

    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2500)

    title = ""
    description = ""
    attachments = []

    try:
        title = page.locator("h1.fOvfyc span").first.inner_text().strip()
    except Exception:
        pass

    try:
        description = page.locator("div.nGi02b").first.inner_text().strip()
    except Exception:
        pass

    try:
        links = page.locator("a.vwNuXe")
        for i in range(links.count()):
            el = links.nth(i)
            name = el.get_attribute("title") or el.inner_text()
            href = el.get_attribute("href")

            if href and "google" in href:
                attachments.append(f"{name} — {href}")
    except Exception:
        pass

    attachments = list(dict.fromkeys(attachments))

    if not title or not description:
        return None

    return {
        "title": title,
        "description": description,
        "attachments": "\n".join(attachments),
    }


def apply_sheet_formatting(sheet, num_rows):
    sheet_id = sheet.id
    end_row = max(num_rows, 2)

    body = {
        "requests": [
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {"red": 0.15, "green": 0.15, "blue": 0.15},
                            "textFormat": {
                                "foregroundColor": {"red": 1, "green": 1, "blue": 1},
                                "bold": True
                            }
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)"
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": 5,
                            "endColumnIndex": 6
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "Submitted"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.85, "green": 0.95, "blue": 0.85},
                                "textFormat": {"bold": True}
                            }
                        }
                    },
                    "index": 0
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": 5,
                            "endColumnIndex": 6
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_CONTAINS",
                                "values": [{"userEnteredValue": "Not Submitted"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.98, "green": 0.86, "blue": 0.86},
                                "textFormat": {"bold": True}
                            }
                        }
                    },
                    "index": 0
                }
            },
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "endRowIndex": end_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": 7
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{
                                    "userEnteredValue": '=AND($F2<>"Submitted",$D2<>"",DATEVALUE(LEFT($D2,10))<TODAY())'
                                }]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.95, "green": 0.75, "blue": 0.75}
                            }
                        }
                    },
                    "index": 0
                }
            },
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 7
                    }
                }
            }
        ]
    }

    sheet.spreadsheet.batch_update(body)


def submit_google_email(sign_in_page, email: str):
    email_locator = sign_in_page.locator('input[name="identifier"]').first
    email_locator.wait_for(state="visible", timeout=15000)
    email_locator.click()
    email_locator.fill("")
    email_locator.fill(email)

    try:
        entered_value = email_locator.input_value()
        log.info(f"Email field value after fill: {entered_value}")
    except Exception:
        pass

    sign_in_page.wait_for_timeout(1000)

    try:
        identifier_next = sign_in_page.locator("#identifierNext").first
        identifier_next.wait_for(state="visible", timeout=10000)
        identifier_next.click()
    except Exception:
        sign_in_page.keyboard.press("Enter")

    sign_in_page.wait_for_timeout(4000)

    try:
        log.info(f"After email submit URL: {sign_in_page.url}")
        log.info(f"After email submit title: {sign_in_page.title()}")
    except Exception:
        pass


def run_scraper(email: str, password: str, sheet_link: str, progress_callback=None):
    sheet_id = extract_sheet_id(sheet_link)
    student_number = extract_student_number(email)
    all_assignments = []

    def update_progress(value: float, message: str):
        log.info(message)
        if progress_callback:
            progress_callback(value, message)

    with sync_playwright() as p:
        update_progress(0.05, "Launching browser")
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()

        def route_handler(route):
            try:
                if route.request.resource_type in {"image", "font", "media"}:
                    route.abort()
                else:
                    route.continue_()
            except Exception:
                route.continue_()

        context.route("**/*", route_handler)

        page = context.new_page()

        update_progress(0.10, "Opening Google Classroom")
        page.goto("https://classroom.google.com", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        sign_in_page = page

        if sign_in_page.locator("input[name='identifier']").count() == 0:
            opened_new_page = False
            possible_sign_in_texts = [
                "text=Sign in to Classroom",
                "text=Go to Classroom",
                "text=Sign in",
            ]

            for selector in possible_sign_in_texts:
                try:
                    if sign_in_page.locator(selector).count() > 0:
                        with context.expect_page(timeout=5000) as new_page_info:
                            sign_in_page.locator(selector).first.click()
                        sign_in_page = new_page_info.value
                        sign_in_page.wait_for_load_state("domcontentloaded")
                        sign_in_page.wait_for_timeout(2000)
                        opened_new_page = True
                        break
                except Exception:
                    pass

            if not opened_new_page:
                sign_in_page.goto("https://accounts.google.com/", wait_until="domcontentloaded", timeout=30000)
                sign_in_page.wait_for_timeout(2000)

        update_progress(0.20, "Entering Google email")
        submit_google_email(sign_in_page, email)

        # If still stuck on the Google identifier page, try the email again once
        if "accounts.google.com/v3/signin/identifier" in sign_in_page.url:
            log.info("Still on Google identifier page, retrying full email once")
            submit_google_email(sign_in_page, email)

        update_progress(0.28, "Detecting login flow")

        google_password_selector = None
        username_selector = None
        password_selector = None

        for _ in range(15):
            sign_in_page.wait_for_timeout(2000)

            google_password_candidates = [
                'input[name="Passwd"]',
                'input[type="password"]:not([aria-hidden="true"])'
            ]

            for sel in google_password_candidates:
                try:
                    locator = sign_in_page.locator(sel).first
                    if locator.count() > 0 and locator.is_visible():
                        google_password_selector = sel
                        break
                except Exception:
                    pass

            if google_password_selector:
                break

            possible_usernames = [
                "#UserName",
                'input[name="UserName"]',
            ]
            possible_passwords = [
                "#Password",
                'input[name="Password"]',
            ]

            for sel in possible_usernames:
                try:
                    locator = sign_in_page.locator(sel).first
                    if locator.count() > 0 and locator.is_visible():
                        username_selector = sel
                        break
                except Exception:
                    pass

            for sel in possible_passwords:
                try:
                    locator = sign_in_page.locator(sel).first
                    if locator.count() > 0 and locator.is_visible():
                        password_selector = sel
                        break
                except Exception:
                    pass

            if username_selector and password_selector:
                break

            try:
                if "accounts.google.com" in sign_in_page.url and sign_in_page.locator("#identifierNext").count() > 0:
                    sign_in_page.locator("#identifierNext").click()
            except Exception:
                pass

        if google_password_selector:
            log.info("Using Google password flow")
            update_progress(0.35, "Entering Google password")

            password_locator = sign_in_page.locator(google_password_selector).first
            password_locator.wait_for(state="visible", timeout=15000)
            password_locator.click()
            password_locator.fill("")
            password_locator.fill(password)

            try:
                if sign_in_page.locator("#passwordNext").count() > 0:
                    sign_in_page.locator("#passwordNext").click()
                else:
                    sign_in_page.keyboard.press("Enter")
            except Exception:
                sign_in_page.keyboard.press("Enter")

        elif username_selector and password_selector:
            log.info(
                f"Using school login flow with username selector {username_selector} "
                f"and password selector {password_selector}"
            )
            update_progress(0.35, "Entering school credentials")

            # Force the exact YRDSB fields first
            try:
                username_locator = sign_in_page.locator("#UserName").first
                username_locator.wait_for(state="visible", timeout=5000)
            except Exception:
                username_locator = sign_in_page.locator('input[name="UserName"]').first
                username_locator.wait_for(state="visible", timeout=15000)

            try:
                password_locator = sign_in_page.locator("#Password").first
                password_locator.wait_for(state="visible", timeout=5000)
            except Exception:
                password_locator = sign_in_page.locator('input[name="Password"]').first
                password_locator.wait_for(state="visible", timeout=15000)

            username_locator.click()
            username_locator.fill("")
            username_locator.type(student_number, delay=80)

            sign_in_page.wait_for_timeout(500)

            password_locator.click()
            password_locator.fill("")
            password_locator.type(password, delay=80)

            sign_in_page.wait_for_timeout(500)
            sign_in_page.keyboard.press("Enter")

        else:
            try:
                html = sign_in_page.content()
                log.info(f"Login page html preview: {html[:5000]}")
            except Exception as e:
                log.info(f"Could not dump login page html: {e}")

            try:
                possible_text = sign_in_page.locator("body").inner_text()
                log.info(f"Login page text preview: {possible_text[:2000]}")
            except Exception as e:
                log.info(f"Could not dump login page text: {e}")

            raise Exception(f"Could not detect Google or school login fields. Final URL: {sign_in_page.url}")

        update_progress(0.45, "Waiting for Google Classroom")
        sign_in_page.wait_for_url("**/classroom.google.com/**", timeout=60000)
        sign_in_page.wait_for_timeout(5000)

        main_page = sign_in_page
        detail_page = context.new_page()

        total_courses = len(COURSES)
        for index, (course_name, url) in enumerate(COURSES.items(), start=1):
            course_progress = 0.45 + (index / max(total_courses, 1)) * 0.35
            update_progress(course_progress, f"Scraping {course_name}")

            bodies = []

            def handle_response(response):
                try:
                    if "batchexecute" not in response.url:
                        return

                    m = re.search(r"rpcids=([^&]+)", response.url)
                    rpc_id = m.group(1) if m else "UNKNOWN"
                    if rpc_id != TARGET_RPC:
                        return

                    body = response.text()
                    bodies.append(body)
                except Exception:
                    pass

            main_page.on("response", handle_response)

            main_page.goto(url, wait_until="domcontentloaded", timeout=30000)
            main_page.wait_for_timeout(5000)
            main_page.reload(wait_until="domcontentloaded", timeout=30000)
            main_page.wait_for_timeout(4500)

            main_page.remove_listener("response", handle_response)

            wrb_entries = []
            for body in bodies:
                wrb_entries.extend(extract_wrb_entries(body))

            submissions = []
            for item in wrb_entries:
                if item["rpc_id"] == TARGET_RPC:
                    submissions.extend(parse_zj93ge_submissions(item["inner"], course_name))

            dedup = {}
            for row in submissions:
                dedup[row["id"]] = row
            submissions = list(dedup.values())

            for sub in submissions:
                try:
                    detail = scrape_assignment_detail(detail_page, sub["course_id"], sub["id"])
                    if detail is None:
                        continue
                except Exception:
                    continue

                all_assignments.append({
                    "id": sub["id"],
                    "title": detail["title"],
                    "description": detail["description"],
                    "attachments": detail["attachments"],
                    "due": sub["due"],
                    "course": sub["course"],
                    "status": sub["status"],
                })

        detail_page.close()
        browser.close()

    if not all_assignments:
        raise Exception("No assignments found — sheet not updated.")

    update_progress(0.88, "Sorting assignments")
    all_assignments.sort(key=sort_due)

    update_progress(0.92, "Opening Google Sheet")
    sheet = get_sheet(sheet_id)
    sheet.clear()

    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    rows = [[
        "Title",
        "Description",
        "Attachments",
        "Due Date",
        "Course",
        "Status",
        "Last Updated"
    ]]

    for a in all_assignments:
        rows.append([
            a["title"],
            a["description"],
            a["attachments"],
            a["due"],
            a["course"],
            a["status"],
            now
        ])

    update_progress(0.97, "Writing assignments to sheet")
    sheet.update(values=rows, range_name="A1")
    sheet.format("A:G", {"wrapStrategy": "WRAP"})
    apply_sheet_formatting(sheet, len(rows))

    update_progress(1.0, "Sheet updated successfully")


def run_scraper_job(job_id: str, email: str, password: str, sheet_link: str):
    try:
        update_job(job_id, status="running", progress=0.01, message="Job started")

        def progress_callback(progress: float, message: str):
            update_job(job_id, status="running", progress=progress, message=message)

        run_scraper(
            email=email,
            password=password,
            sheet_link=sheet_link,
            progress_callback=progress_callback
        )

        update_job(job_id, status="done", progress=1.0, message="Finished")
    except Exception as e:
        update_job(job_id, status="failed", progress=1.0, message=str(e))


@app.get("/")
def root():
    return {"ok": True, "service": "homework-scraper-server"}


@app.post("/start-job", response_model=StartJobResponse)
def start_job(payload: StartJobRequest, background_tasks: BackgroundTasks):
    job_id = create_job()

    background_tasks.add_task(
        run_scraper_job,
        job_id,
        payload.email,
        payload.password,
        payload.sheetLink
    )

    return StartJobResponse(jobId=job_id)


@app.get("/job-status/{job_id}", response_model=JobStatusResponse)
def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        status=job["status"],
        progress=job.get("progress"),
        message=job.get("message")
    )