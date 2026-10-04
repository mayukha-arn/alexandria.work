use anchor_lang::prelude::*;

use crate::{constants::*, state::Ledger};

#[derive(Accounts)]
pub struct Initialize<'info> {
    #[account(mut)]
    pub authority: Signer<'info>,
    #[account(
        init,
        payer = authority,
        space = 8 + Ledger::INIT_SPACE,
        seeds = [LEDGER_SEED],
        bump
    )]
    pub ledger: Account<'info, Ledger>,
    pub system_program: Program<'info, System>,
}

pub fn handle_initialize(ctx: Context<Initialize>) -> Result<()> {
    let ledger = &mut ctx.accounts.ledger;
    ledger.authority = ctx.accounts.authority.key();
    ledger.next_index = 0;
    ledger.bump = ctx.bumps.ledger;
    Ok(())
}
