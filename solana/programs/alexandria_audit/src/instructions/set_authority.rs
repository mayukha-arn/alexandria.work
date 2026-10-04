use anchor_lang::prelude::*;

use crate::{constants::*, error::ErrorCode, state::Ledger};

/// Hand write access to a new key (key rotation).
///
/// Both keys must sign: the current authority proves it consents, and the new one proves it
/// exists and is controlled by someone, so a mistyped address cannot lock the ledger forever.
#[derive(Accounts)]
pub struct SetAuthority<'info> {
    pub authority: Signer<'info>,
    pub new_authority: Signer<'info>,
    #[account(
        mut,
        seeds = [LEDGER_SEED],
        bump = ledger.bump,
        has_one = authority @ ErrorCode::Unauthorized
    )]
    pub ledger: Account<'info, Ledger>,
}

pub fn handle_set_authority(ctx: Context<SetAuthority>) -> Result<()> {
    ctx.accounts.ledger.authority = ctx.accounts.new_authority.key();
    Ok(())
}
