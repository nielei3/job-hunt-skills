# Role Matching Framework

How to evaluate a JD against the user's resume before suggesting edits.

---

## Goal

Produce a **match assessment** that is:
- Honest (don't inflate match %)
- Concrete (cite specific JD requirements and specific user bullets)
- Actionable (tells the user what to do, not just a score)

---

## Output Structure

Always return an assessment in this shape:

```
# <Company> <Role Title> — Match Analysis

## Snapshot
- Location: <city> (vs user's base)
- Level: <inferred L-band>
- Salary range: <if shown>
- Category: <backend / full-stack / infra / ML>

## Overall Match: <N>%

## Strong Matches (table)
| JD Requirement | User's Bullet |
|---|---|
| ... | ... |

## Gaps (split by severity)
### Hard Minimums (blocking)
- <gap>

### Preferred (soft)
- <gap>

### Preferred Qual Hit Rate
<M>/<N> preferred qualifications met

## Narrative Risks
- <risk 1>
- <risk 2>

## Recommendation
- Invest level: <skip / stretch / priority>
- Next action: <variant / cover letter frame / reach out to referrer>
```

---

## Scoring Heuristics

### Strong match (70-85%)
- All hard minimums met
- Most preferred met (>50%)
- Level-appropriate
- No location issues
- Domain adjacent or exact

### Medium match (50-70%)
- Hard minimums mostly met, 1-2 gaps closable with framing
- ~30-50% preferred met
- Level stretch OK
- Location or one major gap requires negotiation

### Weak match (<50%)
- Multiple hard-minimum gaps
- Core domain mismatch (e.g., growth vs infra)
- Level far off
- Recommend skipping unless there's an unusually strong personal connection (referral, mission alignment)

### Perfect match (>85%)
- Rare. Usually signals the resume was already tailored, or the JD was written for someone specific. Investigate whether this is a real opportunity or a placeholder.

---

## Match Weighting

Not all JD requirements are equal. Weight:
- **Required quals**: each hard minimum is a gate. Miss one = serious problem.
- **Tech stack listed in JD**: high weight if it's the primary stack; low weight if it's "nice to have".
- **Level/title**: check if JD's required years match user's experience level.
- **Location**: binary. Either feasible or not.
- **Preferred quals**: soft signal. 4/7 is good; 7/7 is suspicious (did the user write this JD?).

---

## Common JD Red Flags

Call these out to the user:

| Red flag | What it signals |
|---|---|
| "Must be expert in <5 different tech stacks>" | Team doesn't know what they want |
| Vague scope + senior title | Likely a reactive hire covering multiple gaps |
| "0→1 builder" + "scale to billions" in same JD | Impossible phase overlap; pick one |
| No salary range in jurisdictions that require one | Legal/HR laxness |
| Aggressive "always on" / "fast-paced" language | Burnout culture signal |
| Same JD copy as competitor companies | Template hire, not real need |

---

## Narrative Risk Assessment

Beyond keyword match, check for **story risks**:

### Short tenure
If user's current role is <6 months, HM will ask "why leave so soon?" Prepare a clean answer. Don't hide it.

### Title mismatch
If user's current title is "Staff" but JD is "Senior", or vice versa, clarify whether this is a lateral move or up/down. Many target companies have different leveling — a Staff at one company might map to L6 at another, etc.

### Org/team misalignment
Being at TikTok Ad Growth doesn't automatically help you in TikTok Rec Infra. Different orgs inside large companies have different cultures and different hiring standards.

### Location signals
"Austin" on resume while JD is San Jose = relocation question. If user is open to it, state it; if not, skip the role.

### Gap-relative-to-level
Staff+ with no Kubernetes in a K8s-heavy JD is a real gap. At junior levels, this can be dismissed as "will learn"; at Staff+ it's interpreted as "doesn't operate at that abstraction layer".

---

## When to Recommend Skipping

Recommend skipping a role when:
- 2+ hard minimum gaps
- Match < 50% with no narrative bridge available
- Location mismatch user has confirmed they won't relocate for
- JD shows red flags suggesting reactive hire or template posting
- User's energy would be better spent on a higher-match target

Always explain **why** so the user can override if they disagree.

---

## Variant Decision

If proceeding, decide variant type:

### No variant (use base resume)
- Match is strong enough that tailoring adds <5% improvement
- User is early in job hunt and wants volume

### Light variant (reorder bullets, swap 1-2 keywords)
- Match is medium-strong, quick wins available
- Takes 5-10 minutes

### Heavy variant (new summary, rewritten bullets, reordered sections)
- Target company is a priority (user's top 3 list)
- Match needs significant narrative framing

### New primary version (not just a variant)
- Only if user is pivoting domain permanently
