"""Meridian, a payroll and HR software company: its people, documents, channel history and pings.

Used by scripts/make_seed_pdfs.py (renders the documents to PDF) and scripts/seed_meridian.py (loads it all
into a fresh workspace through the normal pipeline: upload, a different person's signed approval, indexing).
"""

# username, role, manager username, IANA time zone
PEOPLE = [
    ("priya.raman", "security_admin", None, "America/New_York"),
    ("ben.foster", "security_admin", "priya.raman", "Europe/London"),
    ("daniel.okafor", "senior_eng", "priya.raman", "America/Chicago"),
    ("sofia.martinez", "senior_eng", "priya.raman", "Europe/Madrid"),
    ("alex.kim", "developer", "daniel.okafor", "America/Los_Angeles"),
    ("jordan.lee", "developer", "daniel.okafor", "Asia/Singapore"),
    ("maya.chen", "support_lead", "priya.raman", "America/New_York"),
    ("marcus.johnson", "support_lead", "priya.raman", "America/Chicago"),
    ("emily.davis", "support_rep", "maya.chen", "America/Denver"),
    ("noah.patel", "support_rep", "maya.chen", "Asia/Kolkata"),
    ("olivia.brooks", "product_manager", "priya.raman", "America/Los_Angeles"),
    ("hannah.wright", "legal_counsel", "priya.raman", "America/New_York"),
    ("grace.liu", "legal_counsel", "priya.raman", "Australia/Sydney"),
    ("james.carter", "executive", "priya.raman", "America/New_York"),
]

# file stem, title, access level, department, uploader, approver, body (light markdown: #, ##, -, paragraphs)
DOCUMENTS = [
    ("payroll-processing-calendar-2026", "Payroll Processing Calendar 2026", "public", "support", "emily.davis", "maya.chen", """
# Payroll Processing Calendar 2026
Owner: Customer Support Operations. Applies to every Meridian Payroll customer on the standard funding schedule.

## How the calendar works
Payroll must be approved in Meridian by **5:00 PM Eastern, two banking days before the pay date**. Approving by the cutoff guarantees that funds are debited one banking day before the pay date and that employees are paid by 9:00 AM local time on the pay date.
Payrolls approved after the cutoff are processed on the next banking day. The pay date moves with them unless the customer has Next-Day Funding enabled.

## Next-Day Funding
Customers on the Plus and Enterprise plans can approve as late as **3:00 PM Eastern, one banking day before the pay date**. Next-Day Funding requires a completed bank verification and at least 90 days of clean payment history. Support can check eligibility on the customer's Billing page.

## Holiday adjustments for Q4 2026
- **Thanksgiving week:** for a Friday, November 27 pay date the cutoff is **Tuesday, November 24 at 5:00 PM Eastern**, because Thursday November 26 is a Federal Reserve holiday.
- **Christmas:** for a Friday, December 25 pay date, the pay date moves earlier to Thursday, December 24, and the cutoff is **Monday, December 21 at 5:00 PM Eastern**.
- **New Year's Day:** for a Friday, January 1, 2027 pay date, the pay date moves to Thursday, December 31, and the cutoff is **Tuesday, December 29 at 5:00 PM Eastern**.
- Veterans Day (November 11) is a banking holiday: any cutoff that falls on it moves one banking day earlier.

## Off-cycle payrolls
Off-cycle runs (bonuses, final paychecks, corrections) follow the same two-banking-day cutoff. For a same-day final paycheck required by state law (for example California or Colorado), direct the customer to issue a manual check and record it in Meridian as a manual payment; do not promise same-day direct deposit.

## What to tell a customer who missed the cutoff
1. Confirm the approval timestamp on the payroll's History tab.
2. Offer to move the pay date to the next banking day, or offer Next-Day Funding if they are eligible.
3. If neither works, they can pay by manual check and record the payroll as manual. Never ask the customer for bank account numbers in chat.
"""),
    ("direct-deposit-failures-runbook", "Direct Deposit Failures: Support Runbook", "public", "support", "noah.patel", "maya.chen", """
# Direct Deposit Failures: Support Runbook
Owner: Customer Support. Use this when an employee says they were not paid by direct deposit.

## First five minutes
1. Open the payroll in Meridian and check the employee's payment status: **Sent**, **Returned**, or **Held**.
2. If the status is **Sent** and the pay date is today, ask the employee to wait until 5:00 PM local time; some receiving banks post deposits late in the day.
3. If the status is **Returned**, read the return code on the payment detail page and follow the table below.
4. If the status is **Held**, the payroll was flagged by risk review. Escalate to the Risk queue; support cannot release held payments.

## Return codes
- **R01, insufficient funds:** the employer's account could not cover the debit. The employer must fund the payroll by wire before Meridian re-sends. Payments are re-sent the same day if the wire arrives before 2:00 PM Eastern.
- **R02, account closed:** the employee's account is closed. The employee updates their bank details in the employee app; the employer then issues the net pay as an off-cycle payment or a manual check.
- **R03, no account / unable to locate:** usually a typo in the account details. Same fix as R02. Do not read account details back to the caller.
- **R29, corporate customer advises not authorized:** the employer's bank blocked Meridian's debit. The employer must add Meridian's ACH company ID to their bank's debit allow list. The ID is shown on the customer's Billing page.

## Funds timing after a return
Returned funds come back to Meridian within 2 to 4 banking days and are credited to the employer's next payroll unless they ask for a refund. Refunds are issued by the Payments team within 5 banking days of a refund request.

## When to escalate to Engineering
Escalate with a ping to **engineering** if more than 20 employees from the same company show Returned with the same code on one pay date, or if any payment shows **Sent** but has no trace number after 24 hours.
"""),
    ("pto-and-leave-policy", "PTO and Leave Policy", "public", "legal", "grace.liu", "hannah.wright", """
# PTO and Leave Policy
Owner: People & Legal. Applies to all Meridian employees in the United States. Effective January 1, 2026.

## Paid time off
- Full-time employees accrue **6.67 hours of PTO per semi-monthly pay period** (20 days a year).
- After 3 years of service, the accrual rises to **8.33 hours per pay period** (25 days a year).
- Unused PTO carries over up to a cap of **120 hours**. Accrual pauses while the balance is at the cap.
- PTO requests of 3 days or more need manager approval at least **2 weeks in advance**.

## Company holidays
Meridian observes 11 paid holidays, including the day after Thanksgiving and a floating holiday each employee chooses.

## Sick leave
Sick leave is separate from PTO: **48 hours per calendar year**, available from the first day of employment, with no doctor's note needed for absences of 3 days or fewer. Where state or city law is more generous, that law applies.

## Parental leave
Birthing and non-birthing parents receive **16 weeks of fully paid parental leave**, to be taken within 12 months of the birth, adoption or foster placement. Leave can be split into up to two blocks.

## Bereavement
Up to **5 paid days** for an immediate family member and 2 days for an extended family member.

## Payout on separation
Accrued, unused PTO is paid out in the final paycheck in every state, whether or not state law requires it.
"""),
    ("expense-reimbursement-policy", "Expense Reimbursement Policy", "internal", "legal", "grace.liu", "hannah.wright", """
# Expense Reimbursement Policy
Owner: People & Legal with Finance. Internal: for Meridian employees only.

## Submitting expenses
Submit expenses in the Meridian expense tool within **30 days** of purchase with an itemized receipt for anything over $25. Approved expenses are reimbursed on the **next regular payroll** as a non-taxable reimbursement line.

## Limits
- **Meals while traveling:** up to $75 per day, alcohol excluded.
- **Client meals:** up to $100 per person, with the client's company noted.
- **Home office stipend:** $500 once in the first 90 days, then $150 per year for equipment.
- **Internet:** up to $50 per month for remote employees.
- **Learning and development:** up to $1,500 per year for courses, books and conferences, with manager approval in advance.

## Travel
Book flights and hotels through the travel portal. Economy class for flights under 6 hours. Hotels up to $250 per night, or $325 in New York and San Francisco.

## What is not reimbursed
Personal entertainment, traffic or parking fines, upgrades, and any expense submitted more than 90 days after purchase.
"""),
    ("postmortem-duplicate-local-tax-withholding", "Postmortem: Duplicate Local Tax Withholding (Oct 2026)", "internal", "engineering", "alex.kim", "daniel.okafor", """
# Postmortem: Duplicate Local Tax Withholding (Oct 2026)
Owner: Payroll Engine team. Severity 2. Status: resolved.

## Summary
On the October 15 pay date, **312 employees across 41 companies** in Ohio and Pennsylvania had a local earned-income tax withheld twice. The cause was a tax-jurisdiction rule change deployed in engine release 4.1.8 that matched both the work-location and the residence jurisdiction for employees whose two addresses resolved to the same school district.

## Timeline (Eastern)
- Oct 14, 16:10: release 4.1.8 deployed with the new jurisdiction matcher.
- Oct 15, 07:40: first support ping about doubled local tax from a customer in Columbus, Ohio.
- Oct 15, 09:05: engineering confirmed the duplicate match and disabled the new matcher with the feature flag **tax.jurisdiction.v2**.
- Oct 15, 13:30: correction payments queued for all affected employees.
- Oct 16, 09:00: correction payments deposited.

## Resolution
- The extra withholding was refunded to employees as a **correction payment on October 16**, and the tax liability was reversed before filing, so no amended filings are needed.
- Engine release **4.1.9** de-duplicates jurisdictions by tax authority code before calculating.

## What support should tell customers
Affected employees were refunded automatically on October 16 and will see a correction line on their pay stub. No action is needed from the employer. If an employee still sees a duplicate on a later pay stub, escalate with a ping to engineering and include the company ID and pay date.

## Follow-ups
- Add a calculation-diff check that blocks a release if more than 0.5% of a sample payroll changes local tax.
- Add Ohio and Pennsylvania school-district fixtures to the regression suite.
"""),
    ("multi-state-withholding-guide", "Multi-State Withholding Compliance Guide", "confidential", "legal", "hannah.wright", "priya.raman", """
# Multi-State Withholding Compliance Guide
Owner: Legal. Confidential: contains our compliance positions and open regulatory matters.

## Reciprocity agreements
States with reciprocity let an employer withhold only for the employee's state of residence. Meridian applies reciprocity automatically for these pairs when the employee files the state's non-residency form:
- Pennsylvania with New Jersey, Ohio, Indiana, Maryland, Virginia and West Virginia.
- Illinois with Iowa, Kentucky, Michigan and Wisconsin.
- Ohio with Indiana, Kentucky, Michigan, Pennsylvania and West Virginia.

## Convenience-of-the-employer rule
New York, Connecticut, Delaware, Nebraska and Pennsylvania may tax remote employees as if they worked at the employer's office when the remote work is for the employee's convenience. Meridian's default is to withhold for the office state in these cases. Customers can override this only with a signed attestation, which Legal keeps on file.

## Open regulatory matters
- **Ohio municipal tax:** we are in discussion with the Regional Income Tax Agency about the October 2026 duplicate local withholding. Our position is that no penalty applies because the liability was corrected before filing. Do not discuss this matter with customers; refer questions to Legal.
- **New Jersey:** awaiting guidance on the 2027 change to the reciprocity form. Expected in the first quarter of 2027.

## Escalation
Any customer question about penalties, audits or notices from a tax agency goes to Legal by ping, never answered by Support directly.
"""),
    ("data-retention-and-deletion-policy", "Customer Data Retention and Deletion Policy", "internal", "security", "ben.foster", "priya.raman", """
# Customer Data Retention and Deletion Policy
Owner: Security. Internal. Supports our SOC 2 Type II and GDPR commitments.

## Retention periods
- **Payroll and tax records:** 7 years after the end of the tax year, as required for employment tax records.
- **Employee bank details:** deleted 90 days after an employee is terminated and their final payment has cleared.
- **Support chat transcripts:** 2 years.
- **Application logs:** 13 months; logs never contain bank or tax identification numbers.
- **Backups:** encrypted, kept for 35 days, then destroyed.

## Deletion requests
A customer administrator can request deletion of a former employee's personal data. Security completes the request within **30 days**, except for payroll and tax records under legal retention, which are restricted from use rather than deleted until the retention period ends.

## Who may access customer data
Engineers access production customer data only through just-in-time access approved by a senior engineer, for a maximum of 4 hours, and every access is logged and reviewed weekly by Security.

## Reporting an incident
Report a suspected data exposure to Security within 1 hour of discovery using the security channel or a ping to security. Do not try to investigate it yourself first.
"""),
    ("executive-compensation-bands-fy2027", "Compensation Bands FY2027", "confidential", "legal", "hannah.wright", "grace.liu", """
# Compensation Bands FY2027
Owner: People & Legal. Confidential: for executives, legal and HR partners only.

## Bands (base salary, US national zone)
- **Support Representative (L2):** $52,000 to $64,000.
- **Support Lead (L4):** $78,000 to $96,000.
- **Software Engineer (L3):** $128,000 to $152,000.
- **Senior Software Engineer (L5):** $168,000 to $201,000.
- **Product Manager (L4):** $142,000 to $170,000.
- **Director (L7):** $215,000 to $258,000.
- **Vice President (L8):** $260,000 to $320,000.

## Zones
New York City, San Francisco and Seattle use the national band plus 12%. Remote employees in other areas use the national band.

## Merit cycle
The FY2027 merit budget is **4.0% of base payroll**, with a further 1.0% reserved for promotions. Merit increases take effect on the first payroll in April 2027.

## Equity
Refresh grants for top performers are reviewed with the merit cycle. Equity guidelines are maintained separately by the compensation committee.
"""),
    ("release-notes-4-2", "Meridian Payroll 4.2 Release Notes", "public", "product", "olivia.brooks", "priya.raman", """
# Meridian Payroll 4.2 Release Notes
Owner: Product. Released September 30, 2026.

## New
- **Earned wage access:** employees can withdraw up to 50% of wages they have already earned before payday, for a flat fee of $2.99 per transfer. Employers enable it under Settings, then Benefits. It does not change the employer's payroll funding.
- **Instant final paychecks:** for terminated employees, employers can now pay the final paycheck to a debit card within 30 minutes, which helps meet same-day final-pay laws in states like California and Colorado.
- **Pay stub translations:** pay stubs are available in Spanish, Vietnamese and Simplified Chinese.

## Improved
- The payroll approval page now shows the funding cutoff for the selected pay date and warns 2 hours before it passes.
- Tax-form downloads (W-2, 1099) are now available as one ZIP for the whole company.

## Fixed
- Overtime for employees with two pay rates in the same week now uses the weighted-average regular rate, as required by the Fair Labor Standards Act.
- Garnishment orders with a start date in the middle of a pay period are now prorated correctly.
"""),
]

# A document kept aside for uploading live (it is not loaded by the seed): a new policy that goes to review.
UPLOAD_SAMPLES = [
    ("remote-work-stipend-2027", "Remote Work Stipend Policy 2027", """
# Remote Work Stipend Policy 2027
Owner: People & Legal. Effective January 1, 2027.

## Monthly stipend
Every employee who works remotely at least three days a week receives a **$75 monthly remote work stipend**, paid as a taxable line on the first payroll of each month. It replaces the separate $50 internet reimbursement.

## Coworking
Employees who live more than 50 miles from a Meridian office may expense a coworking membership of up to **$300 per month** instead of the stipend.

## Equipment
The one-time home office stipend rises to **$750** for employees hired on or after January 1, 2027.
"""),
]

# channel id, author, minutes ago, body
CHAT = [
    ("company", "james.carter", 2950, "Morning all 👋 Huge thanks to everyone who worked through the 4.2 launch. Earned wage access already has 1,200 companies enabled."),
    ("company", "olivia.brooks", 2940, "Release notes for 4.2 are in the knowledge base if customers ask what changed. Instant final paychecks is the big one for California customers."),
    ("company", "maya.chen", 2930, "Support has seen a lot of EWA questions already. The release notes helped a ton, thank you!"),
    ("company", "priya.raman", 1500, "Reminder: SOC 2 evidence collection starts Monday. If you touch customer data, make sure your just-in-time access requests have a ticket linked."),
    ("company", "hannah.wright", 1440, "The 2026 PTO and Leave Policy is live. Parental leave is now 16 weeks for all parents. Ask @alexandria if you have questions about it."),
    ("company", "alex.kim", 300, "Heads up: engine 4.1.9 is rolling out today with the local-tax fix. No action needed from support."),
    ("company", "noah.patel", 120, "Does anyone know if the Thanksgiving cutoff moved this year? Customers keep asking."),
    ("company", "emily.davis", 112, "I think it's in the payroll calendar doc. @alexandria can confirm."),
    ("dept-support", "maya.chen", 2800, "Team: please use the direct deposit runbook for every not-paid ticket this week, especially the R29 ones."),
    ("dept-support", "marcus.johnson", 2790, "Also, any penalty or tax-notice question goes to Legal by ping. Do not answer those ourselves."),
    ("dept-support", "emily.davis", 600, "Got a customer in Columbus asking about doubled local tax on their Oct 15 payroll. Is that the incident from last week?"),
    ("dept-support", "noah.patel", 590, "Yes, I believe engineering wrote a postmortem. I pinged them to double check what we can say."),
    ("dept-engineering", "daniel.okafor", 2700, "4.1.9 is green in staging. Rolling out at 10:00 ET tomorrow behind tax.jurisdiction.v2."),
    ("dept-engineering", "sofia.martinez", 2690, "Approved. I'll watch the calculation-diff dashboard during the rollout."),
    ("dept-engineering", "jordan.lee", 400, "Regression fixtures for the Ohio and Pennsylvania school districts are merged."),
    ("dept-legal", "grace.liu", 1600, "I've uploaded the expense policy update. Hannah, could you review it?"),
    ("dept-legal", "hannah.wright", 1590, "Approved and signed. Thanks Grace."),
    ("dept-product", "olivia.brooks", 900, "Drafting the 4.3 scope: pay stub translations for Korean and Tagalog are top of the list."),
    ("dept-security", "priya.raman", 800, "Weekly access review done: 14 just-in-time sessions, all with tickets. 👍"),
]

# asker, department, title, body, minutes ago, answers [(author, kind, body)], final status
PINGS = [
    ("noah.patel", "engineering", "Doubled local tax on Oct 15 payroll: what do we tell customers?",
     "A customer in Columbus, Ohio says 9 employees had local tax withheld twice on the Oct 15 payroll. Is this the known issue, and is there anything the employer needs to do?",
     585, [("alex.kim", "answer", "Yes, that's the duplicate local withholding incident. Everyone affected was refunded automatically with a correction payment on Oct 16, and the liability was reversed before filing, so the employer doesn't need to do anything. The postmortem is in the knowledge base.")], "resolved"),
    ("emily.davis", "legal", "Customer received a penalty notice from the Ohio RITA",
     "A customer forwarded a notice from the Regional Income Tax Agency about the October local tax. What should I tell them?",
     500, [("hannah.wright", "answer", "Thanks for sending it over rather than answering. Please tell the customer Legal will contact them within one business day, and don't comment on penalties. I've taken it from here.")], "resolved"),
    ("olivia.brooks", "support", "Top customer questions about earned wage access?",
     "For the 4.3 planning doc: what are the most common EWA questions you're getting?",
     260, [("maya.chen", "answer", "Top three: does it change payroll funding (no), what's the fee ($2.99 per transfer), and can employers cap it (not yet, and that's the most requested feature).")], "answered"),
    ("jordan.lee", "security", "Just-in-time access for a garnishment bug",
     "I need read access to one customer's garnishment records to reproduce the mid-period proration bug. Ticket is linked in the request.",
     95, [], "open"),
    ("marcus.johnson", "engineering", "Payments showing Sent with no trace number",
     "Three payments for the same customer show Sent since yesterday but have no trace number. Per the runbook I'm escalating. Company is Brightline Dental, pay date Oct 2.",
     40, [], "open"),
]
