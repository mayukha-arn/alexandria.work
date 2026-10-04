use anchor_lang::prelude::*;

use crate::{
    constants::*,
    error::ErrorCode,
    instructions::write_entry,
    state::{AuditEntry, Ledger},
};

#[derive(Accounts)]
pub struct LogAuditEvent<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,
    #[account(
        mut,
        seeds = [LEDGER_SEED],
        bump = ledger.bump,
        has_one = authority @ ErrorCode::Unauthorized
    )]
    pub ledger: Account<'info, Ledger>,
    #[account(
        init,
        payer = authority,
        space = 8 + AuditEntry::INIT_SPACE,
        seeds = [ENTRY_SEED, ledger.next_index.to_le_bytes().as_ref()],
        bump
    )]
    pub entry: Account<'info, AuditEntry>,
    pub system_program: Program<'info, System>,
}

pub fn handle_log_audit_event(
    ctx: Context<LogAuditEvent>,
    actor_hash: [u8; 32],
    action_hash: [u8; 32],
    payload_hash: [u8; 32],
    dept: [u8; 16],
    required_clearance: u8,
    approver: Pubkey,
) -> Result<()> {
    let bump = ctx.bumps.entry;
    write_entry(
        &mut ctx.accounts.ledger,
        &mut ctx.accounts.entry,
        bump,
        actor_hash,
        action_hash,
        payload_hash,
        dept,
        required_clearance,
        approver,
    )?;
    Ok(())
}
