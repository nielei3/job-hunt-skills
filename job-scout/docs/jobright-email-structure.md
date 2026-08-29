# Jobright Email & Detail Page Structure Documentation

*Generated from analysis of IMAP messages 37, 38, 39, 43, 44, 45 on 2026-04-29*

## 1. Email Types

Jobright sends two types of emails, both from `Jobright Job Alert <noreply@jobright.ai>`:

| Type | utm_source | imp_id contains | Cards | Subject pattern |
|------|-----------|----------------|-------|----------------|
| **Instant Push** | `1121` | `instant_push` | 4–6 | `{Company} just posted a {N}% match {Title} role {time} ago` |
| **Daily Digest** | `1025` | `digest_job_alert` | 10 | `{Company} is hiring for "{Title}" like you — {N}% match from Jobright - {date}` |

Both types use **identical HTML structure** for job cards.

## 2. Email HTML Structure (Top-Level)

```
<html>
  <body>
    <!-- header-section: Jobright logo -->
    <table id="header-section">...</table>

    <!-- paragraph: intro text (digest only) -->
    <p id="paragraph">Explore today's top matches...</p>

    <!-- Repeating job cards, each wrapped in <a> → <td> -->
    <a href="..."><table id="job-section">...</table></a>
    <a href="..."><table id="job-section">...</table></a>
    ...

    <!-- view-more-button -->
    <a id="view-more-button" href="https://jobright.ai/jobs/recommend?utm_source=...">
      View More
    </a>

    <!-- footer-section -->
    <table id="footer-section">
      <a id="unsubscribe-link" href="...">unsubscribe</a>
      <a id="update-job-alert-preference" href="...">update preferences</a>
    </table>
  </body>
</html>
```

## 3. Job Card HTML Structure (id="job-section")

Each job card is a `<table id="job-section">` with this structure:

```html
<table id="job-section" style="border:1px solid #F2F3F4;border-radius:16px;margin-top:8px" width="100%">
  <tbody><tr><td>

    <!-- MAIN CONTENT: id="job-container" -->
    <table id="job-container" style="padding:12px">
      <tbody><tr><td>

        <!-- ROW 1: Company icon + name + match % -->
        <table> <!-- wrapper -->
          <tr><td>
            <table> <!-- inner layout -->
              <tr>
                <!-- LEFT: Icon + Company -->
                <td>
                  <table>
                    <tr>
                      <td id="job-icon-column" style="width:32px">
                        <div id="job-icon">
                          <img id="job-icon-img" alt="Jobright.ai Job Icon"
                               src="{company_logo_url}"
                               style="width:32px;height:32px;border-radius:6px"/>
                        </div>
                      </td>
                      <td width="8px"></td> <!-- spacer -->
                      <td>
                        <p id="job-company-name">{Company Name}</p>
                        <p id="job-company-categories">{Industry} · {Stage}</p>
                      </td>
                    </tr>
                  </table>
                </td>

                <!-- RIGHT: Match percentage -->
                <td align="right" width="48px">
                  <p id="job-match-percentage"
                     style="background:#00F0A033">
                    <span>{N}%</span>
                  </p>
                </td>
              </tr>
            </table>
          </td></tr>
        </table>

        <!-- ROW 2: Job title (clickable link) -->
        <p id="job-title">
          <a href="{jobright_url}" target="_blank">
            {Job Title}
          </a>
        </p>

        <!-- ROW 3: Tags (salary, location, referrals) -->
        <table>
          <tr><td>
            <p id="job-tag">{salary e.g. "$230K/yr - $275K/yr"}</p>  <!-- optional -->
            <p id="job-tag">{location e.g. "Remote" or "Austin, TX"}</p>
            <p id="job-tag">{referrals e.g. "3+ referrals"}</p>  <!-- optional -->
          </td></tr>
        </table>

      </td></tr></tbody>
    </table>

    <!-- FOOTER: Time posted + Early applicant + Apply button -->
    <table style="padding:4px 12px 12px 12px">
      <tr><td>
        <span id="job-time-posted">{N} minutes ago · </span>
        <span id="job-be-an-early-applicant">Be an early applicant</span>
      </td></tr>
      <tr><td>
        <a id="apply-now-button" href="{jobright_url}"
           style="background-color:#00F0A0;width:96px;height:30px;border-radius:15px">
          APPLY NOW
        </a>
      </td></tr>
    </table>

  </td></tr></tbody>
</table>
```

## 4. Element ID Reference

| ID | Tag | Count/card | Content |
|----|-----|-----------|---------|
| `job-section` | `<table>` | 1 | Outer wrapper for entire card |
| `job-container` | `<table>` | 1 | Inner content container |
| `job-icon-column` | `<td>` | 1 | Logo cell |
| `job-icon` | `<div>` | 1 | Logo wrapper |
| `job-icon-img` | `<img>` | 1 | Company logo image (32x32) |
| `job-company-name` | `<p>` | 1 | Company name text |
| `job-company-categories` | `<p>` | 1 | `{Industry} · {Stage}` (e.g. "Big Data · Public Company") |
| `job-match-percentage` | `<p>` | 1 | Match % in `<span>` (e.g. "85%") |
| `job-title` | `<p>` | 1 | Contains `<a>` with job title text and jobright URL |
| `job-tag` | `<p>` | 1–3 | Tags: salary (optional), location, referrals (optional) |
| `job-time-posted` | `<span>` | 1 | Relative time (e.g. "29 minutes ago · ") |
| `job-be-an-early-applicant` | `<span>` | 1 | "Be an early applicant" text |
| `apply-now-button` | `<a>` | 1 | Apply link (same URL as job-title) |

## 5. Tag Patterns (id="job-tag")

Tags appear in a consistent order when present. Not all tags appear in every card:

| Position | Pattern | Examples | Present? |
|----------|---------|----------|----------|
| 1 | Salary | `$230K/yr - $275K/yr`, `$140K/yr - $198K/yr` | Optional (absent if no salary data) |
| 2 | Location | `Remote`, `Austin, TX`, `New York, NY`, `Company - City Campus, US` | Always |
| 3 | Referrals | `3+ referrals`, `10+ referrals`, `1+ referrals` | Optional |

**Parsing heuristics:**
- Salary: contains `$` and `/yr`
- Location: the first tag that doesn't match salary or referrals pattern
- Referrals: matches `\d+\+ referrals`

## 6. URL Structure

Job links in emails follow this pattern:
```
https://jobright.ai/jobs/info/{job_id}?utm_source={source}&imp_id={impression_id}&utm_medium=email
```

Where:
- `{job_id}` = 24-char hex string (MongoDB ObjectId)
- `{source}` = `1121` (instant push) or `1025` (daily digest)
- `{impression_id}` = `{user_id}__{email_type}__{timestamp}_{random}`

The **canonical URL** (for deduplication) is: `https://jobright.ai/jobs/info/{job_id}`

## 7. JSON-LD Schema on Detail Pages

Each `https://jobright.ai/jobs/info/{job_id}` page contains exactly **one** `<script type="application/ld+json">` with a `schema.org/JobPosting` object.

### Complete Schema (all fields observed):

```json
{
  "@context": "https://schema.org/",
  "@type": "JobPosting",
  "title": "[Remote] Staff Machine Learning Engineer, AI Research",
  "description": "<p>HTML description with Responsibilities, Skills, Benefits, Company Overview sections</p>",
  "datePosted": "2026-04-28 20:25:33",
  "employmentType": "FULL_TIME",
  "hiringOrganization": {
    "@type": "Organization",
    "name": "Cribl",
    "sameAs": "https://www.cribl.io",
    "logo": "https://media.licdn.com/dms/image/v2/..."
  },
  "jobLocationType": "TELECOMMUTE",
  "applicantLocationRequirements": {
    "@type": "Country",
    "name": "USA"
  },
  "jobLocation": {
    "@type": "Place",
    "address": {
      "@type": "PostalAddress",
      "addressLocality": "United States",
      "addressCountry": "US"
    },
    "latitude": 43.0059455,
    "longitude": -123.8925908
  },
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {
      "@type": "QuantitativeValue",
      "minValue": 230000,
      "maxValue": 275000,
      "unitText": "YEAR"
    }
  },
  "validThrough": "2026-05-28T20:25:33",
  "industry": "Big Data, Information Technology, Software, Analytics, Real Time, Security",
  "identifier": {
    "@type": "PropertyValue",
    "name": "Cribl",
    "value": "69f117bd8feca91f1491e729"
  }
}
```

### Field Details:

| Field | Type | Notes |
|-------|------|-------|
| `title` | string | Prefixed with `[Remote]` if remote; this is Jobright's cleaned title |
| `description` | string | Full HTML with `<p>`, `<ul>`, `<li>` tags. Sections: Note, Responsibilities, Skills, Benefits, Company Overview |
| `datePosted` | string | Format: `YYYY-MM-DD HH:MM:SS` (UTC) |
| `employmentType` | string | `FULL_TIME` observed |
| `hiringOrganization.name` | string | Company name |
| `hiringOrganization.sameAs` | string | Company website URL |
| `hiringOrganization.logo` | string | LinkedIn CDN logo URL |
| `jobLocationType` | string | `TELECOMMUTE` for remote jobs |
| `applicantLocationRequirements.name` | string | Country name (e.g., `USA`) |
| `jobLocation.address.addressLocality` | string | City/region or `United States` |
| `jobLocation.address.addressCountry` | string | ISO country code (e.g., `US`) |
| `jobLocation.latitude` / `longitude` | float | Can be `0` if unknown |
| `baseSalary.currency` | string | `USD` |
| `baseSalary.value.minValue` | int | Annual salary minimum (e.g., 230000) |
| `baseSalary.value.maxValue` | int | Annual salary maximum (e.g., 275000) |
| `baseSalary.value.unitText` | string | `YEAR` |
| `validThrough` | string | ISO-ish format: `YYYY-MM-DDThh:mm:ss` |
| `industry` | string | Comma-separated industry tags |
| `identifier.name` | string | Company name |
| `identifier.value` | string | 24-char hex job ID (same as URL path) |

### Description HTML Structure Pattern:

```html
<p>Note: {remote/location note}. {Company summary}. {Role summary}.</p>
<br>
<p>Responsibilities</p>
<ul><li>...</li><li>...</li></ul>
<br>
<p>Skills</p>
<ul><li>...</li><li>...</li></ul>
<br>
<p>Benefits</p>
<ul><li>...</li><li>...</li></ul>
<br>
<p>Company Overview</p>
<li>{company description}</li>
```

## 8. Data Extracted from All 6 Emails

### Summary
| MSG ID | Type | Cards | Companies |
|--------|------|-------|-----------|
| 37 | instant_push | 6 | Cribl, Google, Consensus Cloud, Sumo Logic, WEX, Apetan Consulting |
| 38 | instant_push | 4 | OnBoard, Sumo Logic, WEX, Cognichip |
| 39 | digest_job_alert | 10 | Cognichip, Focal Point, Radiant Security, Chewy, Microsoft, Webilent, Penn Mutual, Snowflake, Chainguard, Yahoo |
| 43 | instant_push | 4 | GE HealthCare, Sumo Logic, Snowflake, WEX |
| 44 | instant_push | 6 | Fabric, WEX, Thermo Fisher, Sumo Logic, Snowflake, WEX |
| 45 | instant_push | 6 | Akina, Leidos, Expedia Group, Sumo Logic, Snowflake, WEX |

### All Extracted Job Cards

Total unique job IDs across 6 emails: **24** (36 total entries with duplicates across emails)

#### Key observations:
- "Filler" jobs (Sumo Logic 96%, WEX 91%, Snowflake 96%) repeat across multiple instant_push emails
- The first card in instant_push emails is the "trigger" job that was just posted
- Match percentages range from 64% to 96%
- Salary format is always `$NNK/yr - $NNK/yr` when present
- Location is either `Remote` or `{City}, {State}` format
- Referrals format is `N+ referrals`
- Company categories format is `{Industry} · {Company Stage}` with ` · ` separator

## 9. Parser Implementation Notes

### Email Parsing Strategy:
1. Decode HTML: `msg.get_payload(decode=True).decode('utf-8', errors='replace')`
2. Parse with BeautifulSoup: `soup = BeautifulSoup(html, 'html.parser')`
3. Find all cards: `soup.find_all(id='job-section')`
4. For each card, extract by ID:
   - `job-company-name` → `.get_text(strip=True)`
   - `job-match-percentage` → `.get_text(strip=True)` → strip `%` → int
   - `job-title` → find inner `<a>` → `.get_text(strip=True)` for title, `.get('href')` for URL
   - `job-tag` (find_all) → classify as salary/location/referrals
   - `job-time-posted` → `.get_text(strip=True)` → strip trailing `·`
   - `job-company-categories` → `.get_text(strip=True)` → split on ` · `
   - `job-icon-img` → `.get('src')` for company logo URL

### URL Deduplication:
```python
import re
def canonical_jobright_url(url):
    m = re.search(r'jobright\.ai/jobs/info/([a-f0-9]{24})', url)
    if m:
        return f"https://jobright.ai/jobs/info/{m.group(1)}"
    return url
```

### Tag Classification:
```python
import re
def classify_tags(tag_texts):
    salary = location = referrals = None
    for t in tag_texts:
        if '$' in t and '/yr' in t:
            salary = t.strip()
        elif re.match(r'\d+\+\s*referrals?', t):
            referrals = t.strip()
        else:
            location = t.strip()
    return salary, location, referrals
```

### Email Type Detection:
```python
def detect_email_type(html):
    """Returns 'instant_push' or 'digest_job_alert'."""
    if 'instant_push' in html:
        return 'instant_push'
    elif 'digest_job_alert' in html:
        return 'digest_job_alert'
    return 'unknown'
```

### JSON-LD Extraction from Detail Pages:
```python
import re, json
def extract_jsonld(html):
    m = re.search(
        r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.DOTALL | re.IGNORECASE
    )
    if m:
        return json.loads(m.group(1))
    return None
```
