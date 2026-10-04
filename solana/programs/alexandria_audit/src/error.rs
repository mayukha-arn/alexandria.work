use anchor_lang::prelude::*;

#[error_code]
pub enum ErrorCode {
    #[msg("Only the ledger authority can write audit entries")]
    Unauthorized,
    #[msg("Clearance must be between 0 and 100")]
    InvalidClearance,
    #[msg("Entry counter overflow")]
    CounterOverflow,
}
