pub mod anchor_document;
pub mod initialize;
pub mod log_audit_event;
pub mod set_authority;

pub use anchor_document::*;
pub use initialize::*;
pub use log_audit_event::*;
pub use set_authority::*;

use anchor_lang::prelude::*;

use crate::{constants::MAX_CLEARANCE, error::ErrorCode, state::{AuditEntry, Ledger}};

/// Shared by every instruction that appends an audit entry.
pub(crate) fn write_entry(
    ledger: &mut Account<Ledger>,
    entry: &mut Account<AuditEntry>,
    entry_bump: u8,
    actor_hash: [u8; 32],
    action_hash: [u8; 32],
    payload_hash: [u8; 32],
    dept: [u8; 16],
    required_clearance: u8,
    approver: Pubkey,
) -> Result<u64> {
    require!(required_clearance <= MAX_CLEARANCE, ErrorCode::InvalidClearance);
    let index = ledger.next_index;
    ledger.next_index = index.checked_add(1).ok_or(ErrorCode::CounterOverflow)?;

    entry.index = index;
    entry.actor_hash = actor_hash;
    entry.action_hash = action_hash;
    entry.payload_hash = payload_hash;
    entry.dept = dept;
    entry.required_clearance = required_clearance;
    entry.approver = approver;
    entry.timestamp = Clock::get()?.unix_timestamp;
    entry.bump = entry_bump;
    Ok(index)
}
