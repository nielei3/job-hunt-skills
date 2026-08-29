# hack2hire.com — Platform-Specific Extraction Reference

> Absorbed from the standalone `hack2hire-archiver` skill. This file contains
> hack2hire.com-specific DOM structure, extraction scripts, and pitfalls for
> the course-archiver workflow.

## Platform overview

- **Site**: hack2hire.com — interview question bank (coding + system design)
- **Stack**: Next.js SPA, Ant Design components, CodeMirror editors
- **Auth**: Cookie-based (`ALGRO_TOKEN`), requires pre-authenticated ai-chrome session
- **Content types**: Coding questions (with multi-language solutions) and system design problems

## URL patterns

| Type | List page | Detail page |
|------|-----------|-------------|
| Coding | `hack2hire.com/companies/{company}/coding-questions` (note: plural **s**) | `hack2hire.com/companies/{company}/coding-questions/{id}/practice?questionId={qid}` |
| System design | `hack2hire.com/companies/{company}/system-design` | `hack2hire.com/companies/{company}/system-design/{id}` |

**Pitfall**: Listing page is `coding-questions` (plural), NOT `coding-question` (singular). The singular URL loads but renders an empty `<main>`.

## Coding question DOM structure (Ant Design)

### Tabs
- Uses `ant-segmented`, NOT `role="tab"`
- Click by matching textContent on `.ant-segmented-item`

### Language selector (Java / Python3 / TypeScript / C++)
1. Open dropdown — must use `mousedown` event dispatch, NOT `.click()`. Regular click does nothing.
2. Wait 800-1000ms for dropdown to render in DOM.
3. Click the `.ant-select-item.ant-select-item-option` matching desired language text.
4. Two `.ant-select` elements exist: index [0] = solution code selector, index [1] = practice editor selector. Always use [0].

### Code extraction — CodeMirror scroll-and-collect (PREFERRED)

CodeMirror virtualizes rendering — only visible lines are in DOM. Must scroll through the editor collecting lines by gutter number for dedup:

```javascript
(async function(){
    var editors = document.querySelectorAll('.cm-editor');
    if (editors.length === 0) return JSON.stringify({error: 'no editor'});
    var editor = editors[0];  // First editor = solution code
    var scroller = editor.querySelector('.cm-scroller');
    if (!scroller) return JSON.stringify({error: 'no scroller'});
    
    var allLines = {};
    function collectLines() {
        var gutterEls = editor.querySelectorAll('.cm-lineNumbers .cm-gutterElement');
        var lineEls = editor.querySelectorAll('.cm-line');
        var lineNums = [];
        for (var g of gutterEls) {
            var n = parseInt(g.textContent);
            if (!isNaN(n)) lineNums.push(n);
        }
        for (var i = 0; i < Math.min(lineNums.length, lineEls.length); i++) {
            allLines[lineNums[i]] = lineEls[i].textContent;
        }
    }
    
    scroller.scrollTop = 0;
    await new Promise(r => setTimeout(r, 300));
    collectLines();
    
    var maxScroll = scroller.scrollHeight;
    for (var pos = 200; pos <= maxScroll + 200; pos += 200) {
        scroller.scrollTop = pos;
        await new Promise(r => setTimeout(r, 200));
        collectLines();
    }
    
    var sorted = Object.entries(allLines).sort((a,b) => parseInt(a[0]) - parseInt(b[0]));
    var code = sorted.map(e => e[1]).join('\n');
    return JSON.stringify({lines: sorted.length, code: code});
})()
```

Key details:
- Use `awaitPromise: true` in `Runtime.evaluate` since this is async
- `editors[0]` = solution code viewer. `editors[1]` (if present) = practice editor
- Lines keyed by gutter number → duplicates auto-deduped
- Scroll step 200px, 200ms delay per step
- Wait 4s after language switch before running

### Code extraction — Copy button fallback (UNRELIABLE)

~50% failure rate in batch runs. Use only if scroll-and-collect fails:
1. Clear clipboard with sentinel (`pbcopy` with `__SENTINEL__`)
2. Click `button.ant-btn-circle` (copy button)
3. Wait 1.5s, read clipboard via `pbpaste`
4. Verify clipboard changed from sentinel; retry up to 3 times

### Description extraction

```javascript
(function(){
    var body = document.body.innerText;
    var reportIdx = body.indexOf('\nReport\n');
    if (reportIdx === -1) reportIdx = body.length;
    var lastReported = body.indexOf('Last Reported');
    var descStart = 0;
    if (lastReported > 0) {
        var afterLR = body.indexOf('\n\n', lastReported);
        if (afterLR > 0) descStart = afterLR + 2;
    }
    return body.substring(descStart, reportIdx).trim();
})()
```

### Known tags
Hash Table, Heap, Array, String, Tree, Graph, Dynamic Programming, Stack, Queue, BFS, DFS, Binary Search, Greedy, Trie, Linked List, Sorting, Simulation, Design, Math, Recursion, Backtracking, Sliding Window, Two Pointers, Prefix Sum, Bit Manipulation, Union Find, Monotonic Stack, OOP, Interval, Matrix.

### Solution explanation
After switching to Solution tab, explanation text appears after "Solution Explanation" marker and before "In Progress" or "Debug" markers in body.innerText.

## System design pages

### DOM structure — collapsible chapters (NOT CodeMirror)

Fundamentally different from coding questions:
- **No code editor** — content is pure text with diagrams/tables
- **No Solution/Description tabs** — content is in expandable chapters
- **8 collapsible chapters** (typical): Introduction, Requirements, Data model, API design, High-level design, Deep dives, Other Considerations, Interviewer expectations

### Chapter expansion

Only Chapter 1 expanded on initial load (~6K chars). After expanding all → ~36K chars.

```javascript
// Expand all 8 chapters by clicking their headers
for (var i = 1; i <= 8; i++) {
    var btns = document.querySelectorAll('button');
    for (var b of btns) {
        var t = b.textContent.trim();
        if (t.startsWith(i + '.')) { b.click(); break; }
    }
    await new Promise(r => setTimeout(r, 1500));
}
```

### Content extraction

```javascript
(function(){
    var article = document.querySelector('article');
    if (!article) return '';
    var text = article.innerText;
    var idx = text.indexOf('CHAPTER 1');
    if (idx === -1) idx = text.indexOf('1\n');
    var endIdx = text.indexOf('Similar Questions');
    if (endIdx === -1) endIdx = text.length;
    return text.substring(0, endIdx).trim();
})()
```

### Session/paywall detection (CRITICAL)

```javascript
(function(){
    var text = document.body.innerText;
    var loggedIn = !text.includes('Log In') && !text.includes('Join for free');
    var premium = text.includes('Premium') && !text.includes('Upgrade to premium and unlock');
    var paywalled = text.includes('Upgrade to premium and unlock');
    return JSON.stringify({loggedIn, premium, paywalled});
})()
```

**Silent truncation trap**: When session expires mid-batch, pages still load with 8 chapter buttons — but clicking them returns "not found". Extracted content is ~1.5-2.5K chars (free preview) instead of ~30K. **Always verify file sizes**: free preview = 1-3K, full content = 25-36K.

### System design extraction sequence
1. Navigate to URL (9-10s wait for SPA hydration)
2. Check login/paywall status — abort if expired
3. Extract title and metadata
4. Click all 8 chapter headers (1.0-1.5s delay each, ~12s total)
5. Wait 2s for final render
6. Extract full text via `article.innerText`
7. Post-process: strip sidebar nav, split into chapters, convert to markdown
8. Verify content size — if < 5K chars, flag as potentially paywalled
9. Sleep 10-18s before next question (anti-scraping pacing)

## Pagination
Both coding and system design listing pages may have pagination. Uses `ant-pagination-item` elements. Click `li.ant-pagination-item[title="2"]` for page 2, etc.

## Batch extraction approach
Use websocket batch script pattern (connect to Chrome CDP websocket, iterate with Page.navigate + Runtime.evaluate). ~20s per question with anti-scraping pacing. Use `python3 -u` or `flush=True` for unbuffered output in background mode.

## Output structure

```
Career/Course/hack2hire/{company}-interview/
├── _toc.json
├── README.md
├── 01-coding-questions/
│   ├── 01-{slug}.md
│   └── ...
├── 02-system-design/
│   ├── 01-{slug}.md
│   └── ...
└── images/
```

## Multi-part questions (follow-ups) — MUST archive all parts

Many hack2hire questions have follow-up parts (shown as `+N` suffix on the listing page, e.g. "Design IP Range Iterator+2" = 3 total parts). **Every part must be archived.** A question file missing its follow-ups is incomplete and the user won't discover the gap until interview prep.

### Discovery

On the listing page (`/companies/{company}/coding-questions`), each question link's text ends with `+N` if it has N follow-up parts. Parse this:

```javascript
// On listing page: extract question names and part counts
(function(){
    const links = document.querySelectorAll('a[href*="coding-questions"][href*="practice"]');
    return JSON.stringify(Array.from(links).map(a => {
        const text = a.textContent.trim();
        const match = text.match(/\+(\d+)$/);
        return {
            text: text.replace(/\+\d+$/, '').trim(),
            totalParts: match ? parseInt(match[1]) + 1 : 1,
            href: a.href
        };
    }));
})()
```

### Navigating to each Part

On a question's detail page, follow-up parts appear as `BUTTON.ant-btn.ant-btn-round` elements labeled "Part 2", "Part 3", etc. Clicking one changes the URL's `questionId` parameter.

```javascript
// Click Part N and return the new URL
async function goToPart(n) {
    const buttons = document.querySelectorAll('button.ant-btn-round');
    for (const btn of buttons) {
        if (btn.textContent.trim() === 'Part ' + n) {
            btn.click();
            await new Promise(r => setTimeout(r, 3000));
            return window.location.href;
        }
    }
    return null;
}
```

### Extraction per Part

Each Part has its own Description tab, Solution tab, and code editor — treat it as a separate question. Archive flow per Part:

1. Click the Part button → URL updates with new `questionId`
2. Wait 3-4s for SPA to load the new Part content
3. Extract Description (same JS as single-part questions)
4. Switch to Solution tab → extract explanation text
5. Switch solution code language if needed → extract code (see caveats below)
6. Append to the same output `.md` file as `## Part N: <title>`

### Code extraction caveats for follow-up parts

**Stub-code problem**: Follow-up parts frequently show only a partial solution — just the class that changed (e.g. 25 lines of the `ChatApp` class diff) without repeating unchanged classes (Bot, EventBus, Channel, etc.). This is NOT a scraping bug; the platform itself only displays the delta. Signs of a stub solution:

- scrollHeight is large (e.g. 7350px) but only 25-30 `cm-line` elements exist
- Gutter numbers stay at 1-25 regardless of scroll position
- Code ends mid-method (missing closing braces, `getMessages()`, etc.)

When this happens:
1. Archive whatever code the platform shows — do not fabricate missing code
2. Add a note in the output: `> **Note:** Solution code shows only the changed class. Bot/Event/Channel classes are identical to Part 1.`
3. If the code is clearly truncated mid-method (e.g. stops inside `sendMessage()`), infer the remaining trivial methods (`getMessages`, `registerAllBots`) from the Description + Explanation and add them inside the code block, since the platform's display is broken

**Language selector may not work on follow-up parts**: The `.ant-select-selector` element sometimes doesn't exist on Part 2+ pages (returns `null`). In this case the code displays in whatever language was last active. Workaround: select Java on Part 1 BEFORE clicking Part 2, so the language carries over.

**Copy button unreliable on follow-up parts**: The `ant-btn-circle` copy button exists but clipboard remains unchanged ~70% of the time on Part 2+ pages. Use `.cm-content.innerText` as primary extraction for short stub solutions (< 40 lines), since CodeMirror virtualization is not an issue when the entire solution fits in the viewport.

### Output format for multi-part questions

All parts go in ONE file. Part 1 content is the main `## Description` / `## Solution` sections. Each follow-up appends:

```markdown
---

## Part 2: <subtitle>

**Source:** [hack2hire](<part2_url>)
**Acceptance Rate:** XX% (X.Xk / X.Xk)

### Description
...

### Solution (Java)
...

### Explanation
...
```

### Verification checklist

After archiving a company's questions, verify part completeness:

```bash
# For each question with +N on the listing page, count ## Part headings in the file
for f in *.md; do
  parts=$(grep -c '^## Part' "$f")
  echo "$f: $parts parts"
done
```

Compare against the listing page's `+N` counts. Any mismatch = missing follow-ups.

---

## Completed archives

| Company | Coding Qs | System Design Qs | Date | Notes |
|---------|-----------|-------------------|------|-------|
| OpenAI | 13 (Java) | 13 | 2026-05-03 | Q6-Q13 SD needed re-scrape after session expiry |

## Key pitfalls

1. `<main>` stays empty: SPA hydrates inside `.Loader_overlay__uSK6f` > `main`. If empty after 10s, reload.
2. `.cm-content.innerText` unreliable: Returns only ~34 currently-rendered lines. Always use scroll-and-collect.
3. `mousedown` required for `ant-select`: Regular `.click()` doesn't open dropdown.
4. CDP clipboard access (`navigator.clipboard.readText()`) unreliable — depends on focus state.
5. Python stdout buffering: Use `python3 -u` or `flush=True` when running batch scripts in background.
6. Two-pass approach: First pass batch script gets ~60-70% of codes; second pass with individual CDP calls to fix failures.
7. Premium session expiry mid-batch: 13 system design questions × ~5 min = ~65 min. Sessions can expire. Split into batches (FREE first, Premium second).
8. Stub-code on follow-up parts: Part 2+ solutions often show only the changed class (~25 lines), not the full solution. The scrollHeight is large but the DOM only has 25-30 `cm-line` elements that never change on scroll. This is a platform limitation, not a scraping bug. Archive what's shown and add a note.
9. **Multi-part questions silently incomplete**: The listing page shows `+N` suffix for follow-ups, but navigating to a question only loads Part 1 by default. If you don't explicitly click "Part 2", "Part 3" buttons, follow-ups are silently skipped. Always parse the `+N` count from the listing page and verify each file has all parts archived.
10. **Follow-up part UI differences**: Language selector (`.ant-select-selector`) and copy button (`ant-btn-circle`) are unreliable on Part 2+ pages. Select language on Part 1 before navigating to Part 2. For short stub solutions, use `.cm-content.innerText` directly instead of scroll-and-collect.
