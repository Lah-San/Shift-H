# WA Health – Management of Accrued Leave: research report for an AI leave-cover recommendation agent

Research date: 19 September 2026. All primary documents were downloaded and text-extracted (pdftotext / docx XML) rather than summarised from search snippets. Items marked **[NOT VERIFIED]** could not be fetched.

## 0. Source inventory (verified)

| # | Document | Status / version | URL |
|---|----------|------------------|-----|
| S1 | **MP 0100/18 Management of Accrued Leave Policy** (Workforce and Employment Policy Framework > Industrial Relations) | Effective 22 Nov 2018; **v2.0 amended 23 Feb 2026**, review Feb 2029; approved by DG Dr David Russell-Weisz 13 Nov 2018 | https://www.health.wa.gov.au/~/media/Corp/Policy-Frameworks/Workforce-and-employment/Management-of-Accrued-Leave-Policy/Management-of-Accrued-Leave-Policy.pdf (landing page: https://www.health.wa.gov.au/About-us/Policy-frameworks/Workforce-and-Employment/Mandatory-requirements/Industrial-Relations/Management-of-Accrued-Leave-Policy) |
| S2 | **Employee Leave Management Plan (ELMP) Template** (.docx, "© Department of Health 2026") | Current supporting document to S1 | https://www.health.wa.gov.au/~/media/Corp/Policy-Frameworks/Workforce-and-employment/Management-of-Accrued-Leave-Policy/Supporting/Employee-Leave-Management-Plan-Template.docx |
| S3 | **IRD 07/2024 Excess Leave Management for Senior Medical Practitioners** (Industrial Relations Directive under MP 0025/16) | Effective 9 Jul 2024, review Jul 2027 | https://www.health.wa.gov.au/~/media/Corp/Policy-Frameworks/Workforce-and-employment/Industrial-Relations-Policy/Supporting/IRD-07-Leave-Management-for-Senior-Medical-Practitioners.pdf |
| S4 | **GSLR/PSLR Policy Statement – Management of Accrued Leave in the Public Sector** (whole-of-government; "Cabinet directive") | Issued 23 Mar 2018 (A/ED PSLR Alex Lyon); page last updated 5 Nov 2024 | https://www.wa.gov.au/government/publications/management-of-accrued-leave-the-public-sector ; PDF https://www.wa.gov.au/system/files/2024-10/2018_03_23_pslr_policy_statement_-_management_of_accrued_leave_in_the_public_sector.pdf |
| S5 | **WA Health System – Medical Practitioners – AMA Industrial Agreement 2024** (2024 WAIRC 00992) | Current for doctors incl. Senior Medical Practitioners | https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Doctors/Medical-practitioners-AMA-industrial-agreement-2024.pdf |
| S6 | **WA Health System – ANF – Registered Nurses, Midwives, Enrolled (Mental Health) and Enrolled (Mothercraft) Nurses Industrial Agreement 2024** (2025 WAIRC 00098) | Current for nurses/midwives | https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Nurses-Registered-and-Enrolled-Mental-Health/WA-Health-ANF-Agreement-2024.pdf |
| S7 | **WA Health System – HSUWA – PACTS Industrial Agreement 2024** (2025 WAIRC 00175) | Current for salaried officers / allied health / admin | https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Salaried-officers/WA-HEALTH-SYSTEM---HSUWA---PACTS-INDUSTRIAL-AGREEMENT-2024.pdf |
| S8 | **Purchased Leave Guidelines** (IR Supplementary Information, orig. IC 0157/13–IC 0163/13) | Current version 1/10/2020 | https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Fact-sheets/Purchased-Leave-Guidelines.pdf |
| S9 | **Additional Annual Leave – Shift Workers Guidance Note** (HSU Agreement cl. 39.11) | Issued 30 Sep 2025, effective 8 Sep 2025 | https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Fact-sheets/Additional-Annual-Leave-for-Shift-Workers-Guidance-Note.pdf |
| S10 | **MP 0025/16 Industrial Relations Policy** (landing page listing IRD-01…IRD-07) | Effective 1 Jul 2016 | https://www.health.wa.gov.au/About-us/Policy-frameworks/Employment/Mandatory-requirements/Industrial-Relations/Industrial-Relations-Policy |

**[NOT VERIFIED]** The older stand-alone "Leave Management for Senior Medical Practitioners Policy" (dated 22 Dec 2020) returns HTTP 404 at every known URL; search snippets state it was superseded on 9 Jul 2024 by IRD 07/2024 (S3). The Perth Children's Hospital mirror of the policy page also 404s. No separate WA Health "Leave Liability Guideline", "Annual Leave Guideline" or FAQ document exists on the public site; the mandatory content is entirely in S1 + S2 + the industrial agreements.

---

## 1. Definition of "Excess Leave"

**S1 cl. 7 Definitions** (quoted):

> **Excess Leave** – "An Annual Leave balance in excess of two accrued entitlements and/or a Long Service Leave balance which remains two or three years after the date of entitlement, according to the relevant Industrial Instrument."
>
> **Accrued Leave** – "Leave entitlements which accrue in alignment with an employee's tenure and in accordance with the applicable Industrial Instrument and are able to be accessed by the employee (i.e. taken or paid in lieu of taking leave)."
>
> **Leave liability** – "The total amount of unused Annual Leave and Long Service Leave in hours, or the equivalent dollar amount, which is attributable to all employees in a WA health entity, area or work unit at a given point in time."
>
> **Employee Leave Management Plan** – "An agreed plan between the employee and employer detailing how Excess Leave will be managed and cleared."
>
> **WA Health Monthly Dashboard Report** – "A monthly report provided to payroll certification statement recipients that indicates Excess Leave balances, in accordance with the relevant Industrial Instrument." (The policy body calls this the **Leave Balances Report**.)

**S1 cl. 3 Policy Requirements**: "Subject to the provisions of the relevant Industrial Instrument, WA health entities must implement leave management strategies to ensure that: accrued employee Annual Leave entitlements **do not exceed two entitlements**; and each entitlement to Long Service Leave is **taken within two years of being accrued, or three years if allowed by the relevant Industrial Instrument**."

**S4 (Cabinet directive)**: "employees do not have an annual leave balance that exceeds two accrued entitlements; and each entitlement to long service leave is taken within two years of being accrued." Employers may adopt **lower** limits.

The threshold is therefore expressed in **entitlements**, not fixed hours, so it scales by workforce group and shift status:

| Group (instrument) | One annual entitlement | Excess AL threshold (2 entitlements) | LSL rule |
|---|---|---|---|
| Doctors incl. SMPs – AMA 2024 (S5 cl. 34) | **160 hours (4 weeks × 40 h)** + up to **40 h** additional (on-call: 8 h per 120 h rostered on call; Sunday/PH: 8 h per 7 ordinary shifts; combined cap 40 h) ; WACHS north-of-26th (Sch. 3 cl. 7) +1 week | 320 h (+ any additional) | 13 weeks after 10 yrs then every 7 yrs; take **within three years** of falling due (cl. 38(4)) |
| Nurses/midwives – ANF 2024 (S6 cl. 33) | **20 days (4 weeks)**; continuous shift employee up to **+1 week (5 days)**; regularly on-call up to +5 days; combined cap 5 days (33(14)); north of 26th +1 week | 8 weeks (day) / 10 weeks (continuous shift) ; note cl. 33(3): leave "in no case will … accumulate for more than two years" | 13 weeks after 10 yrs then every 7 yrs; pro-rata after 7 yrs (cl. 35(1)) |
| Salaried officers – HSUWA 2024 (S7 cl. 39) | **4 weeks** + **3.5 days** (non-shift, in lieu of 17.5% loading) ; Shift Workers get loading plus **Additional Annual Leave up to 38 h (5 days)/yr** (cl. 39.11; 2.92 h per 4-week qualifying period with ≥4 qualifying shifts, from 8 Sep 2025 – S9) | 2 × annual entitlement (cl. 39.4) | 13 weeks after 10 yrs then every 7 yrs; clear **within three years** of falling due; accumulation not to exceed **26 weeks** (cl. 47.3) |

**Senior Medical Practitioners (S3 IRD 07/2024)**: "Excess Leave – An annual leave balance in excess of two accrued entitlements and/or a long service leave balance which remains two years after the date of entitlement subject to the relevant industrial instrument." The AMA agreement itself uses two different triggers: cl. 34(13) notice at **more than two years'** accrued AL; cl. 34(12) the Employer may impose conditions (leave at operational convenience) once **more than three years'** entitlement accumulates.

---

## 2. Mandatory requirements on employers / managers

**S1 cl. 3.1 – Chief Executives and Director General** "must reduce and prevent the accrual of Excess Leave by monitoring leave accruals and implementing leave management strategies. Excess Leave can be monitored … through the Health Service Performance Report, via the WA Health Monthly Dashboard Report."

**S1 cl. 3.2 – Executives and managers must**:
- monitor and review excess AL and LSL balances;
- implement action to reduce leave liability in their areas/work units;
- ensure managers and employees **reach agreement** on how leave is managed and when taken (Minimum Conditions of Employment Act 1993 + Industrial Instrument);
- know the Industrial Instrument accrual provisions;
- ensure employees apply for leave per cl. 3.3;
- **when reviewing a leave application and prior to approval, consider**: (i) the Leave Balances Report to reduce any Excess Leave; (ii) organisational priorities; (iii) employee preference for holiday periods; (iv) fairness and equity; (v) financial impact (e.g. cash-out); (vi) relevant industrial provisions;
- ensure approved leave is recorded in the HR information system;
- **develop an Employee Leave Management Plan with employees who have an Excess Leave accrual**.
- "Managers authorising payroll certification statements must review, as soon as practicable, the **monthly** Leave Balances Report."

**When an ELMP must exist**: as soon as an Excess Leave accrual exists (cl. 3.2 last bullet and cl. 3.3 last bullet). The policy sets no fixed clearance period beyond "as soon as practical"; the ELMP template carries its own "ELMP duration" field. Instrument-level timeframes: AMA cl. 34(13) – reduce **within a 12-month period** under a leave management plan agreed with the Head of Department/Medical Director; HSUWA cl. 39.4 – take sufficient leave **prior to the next entitlement becoming due**; cl. 39.7 – may be required to take **double** the annual entitlement each year until under two years.

**Directing / requiring leave** (instrument-specific):
- AMA cl. 34(12): after deferment leads to >3 years, Employer "may impose conditions … including that it be taken at the operational convenience of the Hospital"; cl. 38(3)–(4): LSL "at the convenience of the Employer … within three years"; cl. 61(2)(b)(v): practitioners with >2 years' AL "will apply to take and will take at the operational convenience of the hospital sufficient leave".
- ANF cl. 33(20)–(21): AL **in excess of 10 weeks** "will be taken at the operational convenience of the Employer. The minimum period to be taken will be five days" – **unless** the employee's leave application was denied in the preceding 12 months.
- HSUWA cl. 39.2(f) and 47.3(c): if the employee refuses to discuss leave, "the Employer may roster the employee off for a period of Annual Leave / Long Service Leave"; cl. 39.4(a): employee with >2 years' AL "who has been advised accordingly by the Employer, may be required to take sufficient leave".
- No instrument fixes a statutory notice period for a *direction*; MCE Act s.24–25 (not fetched) governs; the policy requires *agreement* first.

**Review cycles / reporting**: monthly Leave Balances Report to managers (S1 3.2); AMA cl. 34(10) Heads of Department review each practitioner's AL entitlement **annually**; S1 cl. 4 – entities give leave liability balance/trend data per leave type to a CFO representative **monthly**; System Manager exports monthly GL leave liability into the WA Health Power BI Dashboard shared with Government; S4 – each agency's AL/LSL liability goes to **Cabinet every six months** and is intended to be published; S3 – HSPs must make any ELMP available to the System Manager on request.

---

## 3. Employee obligations (S1 cl. 3.3)

Employees must: monitor their AL/LSL balances; know their instrument entitlements; "provide management with **sufficient notice** of future leave requests, to enable adequate workforce planning"; comply with mandatory leave booking by submitting a timely leave application; "clear leave within a reasonable time"; and "complete an Employee Leave Management Plan if Excess Leave accrual exists, and in consultation with their manager, clear Excess Leave as soon as practical."

Minimums per year: HSUWA cl. 39.5 – if the employee fails to take required leave, excess may be **paid out**, "provided that the employee will be required to take **at least two weeks' leave in any anniversary year**"; HSUWA 39.2(b) employees are "expected to take Annual Leave in the year immediately following the anniversary date"; ANF cl. 41(2) cash-out only if **minimum four weeks' leave** is taken in the calendar year; AMA cl. 34(25)/HSUWA 39.21 "less leave, more pay" requires ≥4 weeks available. Consequences: pay-out of excess (HSUWA 39.5), being rostered off (HSUWA 39.2(f)), leave at operational convenience (ANF 33(20), AMA 34(12)).

---

## 4. Employee Leave Management Plan (ELMP) template – every field (S2, verbatim order)

Preamble: "The development of the ELMP must consider: the employee's current leave balances, including leave that will accrue during the ELMP; leave management strategies to be implemented to clear Excess Leave; and commitment to review the ELMP regularly … Any revision to the original ELMP … should be submitted to the relevant authorised delegate for subsequent approval."

1. **Personal Details**: Employee Name | Position Title, Division | Employee Number | ELMP duration
2. **Calculation of leave to be reduced** (columns: Annual Leave, Long Service Leave): Current excess leave balance; Leave to accrue during the ELMP; Total leave balance to be cleared
3. **Leave schedule** (repeating rows): Leave booking period | Leave type (e.g. Annual Leave, Long Service Leave) | Number of days/weeks | Date leave application lodged | Total → **Total leave booked to clear**
4. **Leave cash out** (repeating rows): Leave Type | Number of days/weeks | Industrial conditions met (Yes/No) | Date cash out application lodged with HSS | Total → **Total leave to be cashed out**
5. **Final calculation**: ☐ "Accrued annual leave is reduced to less than two accrued entitlements: Yes / No / N/A"; ☐ "Long service leave is scheduled within two or three years of being accrued (dependent on the relevant Industrial Instrument): Yes / No / N/A"; "If no, comment: ____"
6. **Endorsement** (signature, position title, date ____/____/______ for each): Employee; Manager; Authorised Delegate
7. **Next Actions Checklist**: ☐ All leave and/or cash out applications are approved by the authorised delegate; ☐ All leave and/or cash out applications are submitted to Payroll for processing; ☐ Employee and Manager retain a copy of the ELMP; ☐ Review meeting is scheduled
8. **Contact**: Health Support Services for balances/projections; local industrial relations team for instrument interpretation.

---

## 5. Roster / service needs vs leave liability

- S1 cl. 3.4 (prevent): "Provide training and development to enable efficient backfill of temporary vacancies"; "Regularly assess staffing levels and leave patterns to maintain safe service delivery and minimise operational disruption"; "Plan for peak periods or seasonal fluctuations … appropriately staffing departments in anticipation of both planned and unplanned leave"; "Encourage early leave scheduling"; "Ensure employees schedule future leave before it becomes excessive"; monitor leave portability on transfer/secondment.
- S1 cl. 3.4 (reduce): "considering the necessity of backfilling, such as **not backfilling a vacancy of less than two weeks**"; "where regular backfill is required, consider engaging **permanent relief staff**"; schedule leave "during periods of low demand"; closedown/slowdown at Christmas/New Year (subject to consultation and notice); consider cash-out; weigh purchased-leave requests against existing liability.
- AMA cl. 34(14): "The Employer undertakes to ensure adequate staffing levels to enable practitioners to take their accrued annual leave, provided leave will **not be back filled for periods of two weeks or less** except on urgent clinical or service grounds and only on the approval by the relevant … delegated authority." Cl. 61(2)(b)(v) repeats this at Executive Director level.
- ANF cl. 33(1)(c)–(e) and 35(3)–(5): "An Employer **must not unreasonably refuse** an employee's request"; where "operational requirements" is the reason, "the Employer will provide details of those operational requirements."
- HSUWA cl. 18.3(a): rostering "will determine resources by allocating the appropriate number of employees, with the right level of experience, across teams"; 18.3(e): when a roster change creates a vacant shift the Employer "will endeavour to offer the shift to suitable employees on a basis which promotes equity and the preference for permanent and direct employment."
- Definition of "operational requirements" (AMA 34(23)(e), HSUWA 39.19(e), S8 s.6): availability of suitable leave cover; cost implications; impact on client/patient service requirements; impact on the work of other employees; the employee's existing leave liabilities.

---

## 6. Lead times, approval authority, refusal, cash-out, half/double pay, purchased leave

| Topic | AMA 2024 (doctors) | ANF 2024 (nurses) | HSUWA 2024 (salaried) |
|---|---|---|---|
| Employer response time | cl. 34(11): confirm in writing **within two weeks** of written application | cl. 33(1)(b): respond **within 14 days**; refusal in writing with reasons within 14 days | cl. 39.2(e): respond within 14 days; refusal in writing with reasons |
| Leave accrued >12 months ago | cl. 34(16): Employer **will not refuse** at any time suitable to practitioner, given **≥2 weeks' written notice** | cl. 33(16): after 12 months, take in one period or two periods of ≥2 weeks; smaller portions by agreement | cl. 39.2(g): Employer **is not to refuse**, given ≥2 weeks' notice |
| LSL notice | cl. 38(1)(d): **at least three months' notice** | no fixed notice; min periods of one day (35(2)(a)) | consultation; min one day (47.2(a)) |
| Half / double pay | cl. 34(24) twice the AL period at half pay; LSL cl. 38(5) half or double pay | cl. 33(19) AL at half pay; LSL 35(2)(b)–(d) half/double | cl. 39.20 AL at half **or double** pay; LSL 47.2(b)–(c) |
| Cash-out | cl. 34(15) AL above **one year's** entitlement by written agreement; cl. 38(13) LSL any amount; S3: allowances (private practice/PD) must be paid on cash-out under an approved ELMP | cl. 41: entitlement to cash out **2 weeks per FY** of AL >1 year's entitlement or LSL >2 years after entitlement; must leave ≥4 weeks to take in the calendar year; cost is **not** a valid refusal reason | cl. 39.5/39.8 pay-out of excess if ≥2 weeks taken; 39.21 forfeit 1–2 weeks' accrual for pay |
| Purchased leave | cl. 34(23) 42/52 (up to 10 weeks), subject to operational requirements, 12-month blocks | cl. 41 (2018) 42/52 per S8 | cl. 39.19, 42/52 |
| Roster posting | on-call rosters ≥14 days ahead where practicable (cl. 33(1)(f)) | 14-day roster posted **28 days** prior, never <14 days; alterable for illness/emergency (cl. 32(18)) | published ≥**14 days**, where possible 28 days; altered only for unforeseen circumstances (cl. 18.3) |

Approval authority: the policy uses "manager" and "authorised delegate" (ELMP endorsement chain Employee → Manager → Authorised Delegate); AMA leave plans are agreed with the **Head of Department or Director of Medical Services/Medical Director**; backfill of ≤2 weeks needs hospital/health-service delegated authority or Executive Director approval. Purchased leave (S8 s.6) is approved by the Employer "taking into consideration operational requirements and the employee having met the requirements of the applicable Leave Management Policy"; a new application is required each 12 months; leave must be used within 12 months or paid out.

---

## 7. Accrual rules referenced

- **Annual leave**: 4 weeks base for all three groups (AMA 160 h; ANF 20 days; HSUWA 4 weeks + 3.5 days for day workers). Shift/on-call add-ons: AMA up to 40 h; ANF continuous shift employees up to +1 week and on-call up to +5 days, combined cap 5 days; HSUWA shift workers AAL up to 38 h/yr. North of the 26th parallel: +1 week (ANF 33(15); AMA Sch. 3 cl. 7). Accrual is pro-rata weekly and cumulative. No WA Health instrument reviewed provides a flat "6 weeks for 7-day shift workers"; the 5th week for continuous shift nurses is the closest equivalent.
- **Long service leave**: 13 weeks after **10 years** continuous service, then 13 weeks after each further **7 years**; pro-rata access after 7 years in the first period (all three instruments); casuals 13 weeks after 10 years (ANF 37(1), HSUWA 48.1). Take within 3 years (AMA 38(4); HSUWA 47.3, cap 26 weeks) – hence the policy's "two or three years".

---

## 8. Related policies in the Workforce and Employment framework (Industrial Relations)

Listed on the framework page: **Classification Policy MP 0102/18**; **Industrial Relations Policy MP 0025/16** (with IRD-01 Interpreters, IRD-02 Travelling Allowance, IRD-03 Patient Escorting for Nurses, IRD-04 Unauthorised Stoppages, IRD-05 Medical Advisory Committee, IRD-06 Interns to RMO, **IRD-07 Excess Leave for SMPs**); **Management of Accrued Leave Policy MP 0100/18**; **Managing Unsatisfactory and Substandard Performance MP 0041/16**; **Transition of Fixed Term Senior Practitioners to Permanency MP 0182/24**. HRM policies: Recruitment, Selection and Appointment MP 0033/16; Equal Opportunity MP 0118/19; Grievance Resolution MP 0116/19; Criminal Record Screening MP 0189/25; Working with Children MP 0176/22. There is **no stand-alone WA Health "Hours of Work", "Rostering", "Casual Employment" or "Flexible Working" mandatory policy** – those rules live in the industrial agreements (e.g. HSUWA cl. 16 flexible work, cl. 18 rosters; ANF cl. 29 flexibility in hours and rostering, cl. 32 rostering; AMA cl. 7 agreement flexibility) and in **MP 0187/24 Nurse/Midwife to Patient Ratios Policy** (Clinical framework; amended 16 Feb 2026) which constrains nursing cover.

---

## RULES (machine-readable)

Format: `RULE_ID | rule text | source | how the cover-recommendation agent applies it`

- R01 | Excess annual leave = balance > two accrued annual entitlements (entitlement is instrument-specific: AMA 160 h + up to 40 h extra; ANF 20 days + up to 5 days; HSUWA 4 wks + 3.5 days or shift AAL ≤38 h). | S1 cl.3, cl.7 "Excess Leave"; S4 Directive | Compute `excess_al = balance_hours - 2*annual_entitlement_hours(role, shift_status)`; flag employee as EXCESS when > 0.
- R02 | Excess LSL = any LSL entitlement not taken within 2 years of falling due (3 years where the instrument allows: AMA cl.38(4), HSUWA cl.47.3). | S1 cl.3, cl.7; S5 cl.38(4); S7 cl.47.3 | Flag LSL entitlements aged >24 months (nurses) / >36 months (doctors, salaried officers) as EXCESS; HSUWA hard cap 26 weeks.
- R03 | An Employee Leave Management Plan must be created whenever Excess Leave exists; employee, manager and authorised delegate sign; review meeting scheduled. | S1 cl.3.2, 3.3; S2 | If EXCESS and no ELMP on file, recommend "initiate ELMP" alongside the cover decision; pre-populate S2 fields from balances.
- R04 | Managers must review the monthly Leave Balances Report and consider it, plus organisational priorities, employee holiday preference, fairness/equity, financial impact and industrial provisions, before approving leave. | S1 cl.3.2 | Rank/annotate every recommendation with these six factors; never rely on service need alone.
- R05 | Leave that accrued more than 12 months earlier cannot be refused if the employee gives ≥2 weeks' notice (doctors and salaried officers). | S5 cl.34(16); S7 cl.39.2(g) | If `requested_leave <= balance_older_than_12m` and notice ≥14 days, treat approval as mandatory: the agent must find cover, not recommend refusal.
- R06 | Employer must respond to an annual leave request within 14 days and give written reasons for refusal; "operational requirements" must be detailed; refusal must not be unreasonable. | S6 cl.33(1)(b)-(e), 35(3)-(5); S7 cl.39.2(e); S5 cl.34(11) | Generate the written-reasons text (specific roster gap, skill mix, ratios) whenever recommending "cannot accommodate"; enforce a 14-day SLA on pending requests.
- R07 | Nurses' annual leave > 10 weeks is taken at the operational convenience of the Employer in blocks ≥5 days, unless a request was denied in the prior 12 months. | S6 cl.33(20)-(21) | For nurses with AL >10 weeks, agent may propose employer-chosen dates (≥5 days) into low-demand periods; suppress this if a denial exists in last 12 months.
- R08 | Practitioner with >2 years' AL must be notified in writing to reduce within 12 months under a plan agreed with HoD/Medical Director; >3 years allows conditions on taking leave. | S5 cl.34(12)-(13), 61(2)(b)(v) | Auto-draft notification; set ELMP duration = 12 months; escalate to HoD.
- R09 | Salaried officers with >2 years' AL may be required to take sufficient leave before next entitlement falls due, or double the annual entitlement each year until below threshold; failure → pay-out of excess, but ≥2 weeks' leave per anniversary year must still be taken. | S7 cl.39.4, 39.5, 39.7, 39.8 | For HSUWA staff, propose leave volume = max(excess, entitlement) per year; enforce ≥2 weeks/year in the plan.
- R10 | Vacancies of ≤2 weeks are generally not backfilled (policy strategy; AMA: not backfilled except on urgent clinical/service grounds with delegated approval). | S1 cl.3.4; S5 cl.34(14), 61(2)(b)(v) | Default cover model for ≤10 working days = internal redistribution; recommend agency/relief backfill only with an "urgent clinical grounds" justification and delegate approval flag.
- R11 | Where regular backfill is required, consider permanent relief staff; encourage leave in low-demand periods; plan for seasonal peaks and Christmas/New Year closedowns. | S1 cl.3.4 | Prefer relief-pool candidates as cover; score requested dates against demand calendar; nudge excess-leave employees toward low-demand windows.
- R12 | Employees with Excess Leave should be prioritised/encouraged to take leave; leave liability reduction is a mandatory objective monitored monthly and reported to Cabinet six-monthly. | S1 cl.3.1, 3.2, 4; S4 | Tie-break competing requests for the same window in favour of the higher excess-leave balance (after fairness/equity check R04).
- R13 | Rosters: HSUWA published ≥14 days (ideally 28) before start; ANF 14-day roster posted 28 days prior (min 14); AMA on-call rosters ≥14 days; changes only for unforeseen absence/emergency. | S7 cl.18.3; S6 cl.32(18); S5 cl.33(1)(f) | Leave requests inside the published-roster window need explicit roster-change justification; requests ≥28 days out are "plannable" and should default to approve-with-cover.
- R14 | Employees must give sufficient notice; LSL for doctors requires ≥3 months' notice unless agreed. | S1 cl.3.3; S5 cl.38(1)(d) | Compute lead-time score; for doctor LSL <3 months' notice, mark as "requires agreement".
- R15 | Half-pay (double period) or double-pay (half period) variants exist for AL and LSL, subject to operational requirements and Employer agreement. | S5 cl.34(24), 38(5); S6 cl.33(19), 35(2); S7 cl.39.20, 47.2 | When dates cannot be accommodated, offer alternatives: same absence at half pay clearing more balance, or shorter absence at double pay clearing the same balance.
- R16 | Cash-out: nurses entitled to 2 weeks/FY (AL >1 year's entitlement or LSL >2 years old) provided ≥4 weeks' leave taken that calendar year, cost not a valid refusal; doctors may cash out AL above one year's entitlement and any LSL; SMP cash-out under an ELMP must include private-practice/PD allowances. | S6 cl.41; S5 cl.34(15), 38(13); S3 | Include cash-out as a liability-reduction option in the ELMP output, with the industrial preconditions check ("Industrial conditions met Y/N" field).
- R17 | Purchased leave (42/52) is subject to operational requirements and the employee's existing leave liability; not to be used instead of annual leave. | S1 cl.3.4; S8 s.4, 6, 8; S5 cl.34(23) | Down-rank purchased-leave requests from employees with EXCESS status; recommend clearing accrued leave first.
- R18 | "Operational requirements" = availability of suitable leave cover; cost; impact on patient/client services; impact on other employees' work; the employee's existing leave liability. | S5 cl.34(23)(e); S7 cl.39.19(e); S8 s.6 | Structure every "cannot accommodate" explanation around exactly these five elements.
- R19 | Roster alterations to fill a gap should offer shifts to suitable employees on an equitable basis preferring permanent/direct employment. | S7 cl.18.3(e) | Cover ranking order: permanent staff on unit → permanent relief pool → part-timers' extra hours → casual → agency.
- R20 | Continuous shift status (and thus the 5th week/AAL) depends on rostered shift pattern; losing status for operational reasons does not reduce accrual. | S6 cl.33(8); S9 s.2 | Determine entitlement hours per employee from roster pattern, not job title.
- R21 | Nurse/midwife-to-patient ratios (MP 0187/24) constrain minimum cover on wards. | Clinical framework MP 0187/24 (fetched incidentally) | Never recommend cover that drops a ward below mandated ratio; treat ratio breach as a valid, detailed operational-requirement reason.
- R22 | Policy yields to the Industrial Instrument or law wherever inconsistent. | S1 cl.1 | Instrument-specific rules (R05-R09, R13-R16) override generic policy heuristics when they conflict.
