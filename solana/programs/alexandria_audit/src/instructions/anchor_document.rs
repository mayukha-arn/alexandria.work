use anchor_lang::prelude::*;

use crate::{
    constants::*,
    error::ErrorCode,
    instructions::write_entry,
    state::{AuditEntry, DocRecord, Ledger},
};

/// Record an approved document version and its audit entry atomically.
/// Anchoring the same hash twice fails (the DocRecord PDA already exists), so a
/// version can only ever be approved once.
#[derive(Accounts)]
#[instruction(doc_hash: [u8; 32])]
pub struct AnchorDocument<'info> {
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
    #[account(
        init,
        payer = authority,
        space = 8 + DocRecord::INIT_SPACE,
        seeds = [DOC_SEED, doc_hash.as_ref()],
        bump
    )]
    pub doc_record: Account<'info, DocRecord>,
    pub system_program: Program<'info, System>,
}

pub fn handle_anchor_document(
    ctx: Context<AnchorDocument>,
    doc_hash: [u8; 32],
    parent_hash: [u8; 32],
    actor_hash: [u8; 32],
    action_hash: [u8; 32],
    dept: [u8; 16],
    required_clearance: u8,
    approver: Pubkey,
) -> Result<()> {
    let entry_bump = ctx.bumps.entry;
    let index = write_entry(
        &mut ctx.accounts.ledger,
        &mut ctx.accounts.entry,
        entry_bump,
        actor_hash,
        action_hash,
        doc_hash, // the entry's payload is the document itself
        dept,
        required_clearance,
        approver,
    )?;

    let doc = &mut ctx.accounts.doc_record;
    doc.doc_hash = doc_hash;
    doc.parent_hash = parent_hash;
    doc.approver = approver;
    doc.entry_index = index;
    doc.timestamp = ctx.accounts.entry.timestamp;
    doc.bump = ctx.bumps.doc_record;
    Ok(())
}
