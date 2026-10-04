use anchor_lang::prelude::*;

#[constant]
pub const LEDGER_SEED: &[u8] = b"ledger";

#[constant]
pub const ENTRY_SEED: &[u8] = b"entry";

#[constant]
pub const DOC_SEED: &[u8] = b"doc";

/// Highest clearance level (0-100 scale).
#[constant]
pub const MAX_CLEARANCE: u8 = 100;
