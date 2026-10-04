pub mod constants;
pub mod error;
pub mod instructions;
pub mod state;

use anchor_lang::prelude::*;

pub use constants::*;
pub use instructions::*;
pub use state::*;

declare_id!("Cgnt5epauqrsHCF9BhstkLyUVLjCnGP865bLJEqs22Y6");

#[program]
pub mod alexandria_audit {
    use super::*;

    /// One-time setup: the signer becomes the ledger authority.
    pub fn initialize(ctx: Context<Initialize>) -> Result<()> {
        crate::instructions::initialize::handle_initialize(ctx)
    }

    /// Rotate the write key. Needs signatures from both the current and the new authority.
    pub fn set_authority(ctx: Context<SetAuthority>) -> Result<()> {
        crate::instructions::set_authority::handle_set_authority(ctx)
    }

    /// Append an audit entry (authority only).
    pub fn log_audit_event(
        ctx: Context<LogAuditEvent>,
        actor_hash: [u8; 32],
        action_hash: [u8; 32],
        payload_hash: [u8; 32],
        dept: [u8; 16],
        required_clearance: u8,
        approver: Pubkey,
    ) -> Result<()> {
        crate::instructions::log_audit_event::handle_log_audit_event(
            ctx, actor_hash, action_hash, payload_hash, dept, required_clearance, approver,
        )
    }

    /// Record an approved document version + its audit entry (authority only).
    pub fn anchor_document(
        ctx: Context<AnchorDocument>,
        doc_hash: [u8; 32],
        parent_hash: [u8; 32],
        actor_hash: [u8; 32],
        action_hash: [u8; 32],
        dept: [u8; 16],
        required_clearance: u8,
        approver: Pubkey,
    ) -> Result<()> {
        crate::instructions::anchor_document::handle_anchor_document(
            ctx, doc_hash, parent_hash, actor_hash, action_hash, dept, required_clearance, approver,
        )
    }
}
