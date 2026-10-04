import type { AskDone, Source } from "./api";

const CUTOFF = "What is Meridian's payroll cutoff for Thanksgiving week?";
const R03 = "A customer's direct deposit was returned with code R03. What should I tell them?";
const RELEASE = "What changed in Meridian Payroll 4.2?";
const UNKNOWN = "Who owns the 2027 international payroll launch?";

export const SUGGESTIONS: Record<string, string[]> = {
  support: [CUTOFF, R03, UNKNOWN],
  developer: [RELEASE, CUTOFF, UNKNOWN],
  executive: [RELEASE, CUTOFF, UNKNOWN],
};

function cited(answer: string, title: string, department: string, id: string): AskDone {
  const source: Source = { n: 1, chunk_id: id, doc_hash: id, source: `file:${title}.pdf`, department, verification: null };
  return { answer, sources: [source], grounded: true, warnings: [], persona: "", metrics: { retrieved: 1 } };
}

export const FEATURED_ANSWERS: Record<string, AskDone> = {
  [CUTOFF]: cited("For a **Friday, November 27** pay date, approve payroll by **Tuesday, November 24 at 5:00 PM Eastern**. Thanksgiving Day is a banking holiday, so the cutoff moves earlier. [1]", "Payroll Processing Calendar 2026", "support", "payroll-calendar"),
  [R03]: cited("**R03 means the receiving bank could not locate the account.** Ask the employee to update their bank details in the employee app. The employer should then issue the net pay through an off-cycle payment or a manual check. Do not read account details back to the caller. [1]", "Direct Deposit Failures - Support Runbook", "support", "direct-deposit-runbook"),
  [RELEASE]: cited("Meridian Payroll 4.2 added earned wage access, instant final paychecks to debit cards, and translated pay stubs. It also improved cutoff warnings and bulk tax-form downloads, and fixed weighted-average overtime and mid-period garnishments. [1]", "Meridian Payroll 4.2 Release Notes", "product", "release-notes-4-2"),
  [UNKNOWN]: { answer: "Insufficient verified documentation available in the enterprise knowledge base.", sources: [], grounded: true, warnings: [], persona: "", metrics: { retrieved: 0 } },
};
