use anchor_lang::prelude::*;

/// Singleton config. Only `authority` (the Alexandria backend key) may append.
/// PDA: ["ledger"]
#[account]
#[derive(InitSpace)]
pub struct Ledger {
    pub authority: Pubkey,
    pub next_index: u64,
    pub bump: u8,
}

/// One immutable audit record. Only hashes are stored: the plaintext lives in the
/// organisation's private, encrypted database. `dept` and `required_clearance` are
/// the only readable fields; the UI redacts the rest below that clearance.
/// PDA: ["entry", index_le_bytes]
#[account]
#[derive(InitSpace)]
pub struct AuditEntry {
    pub index: u64,
    pub actor_hash: [u8; 32],
    pub action_hash: [u8; 32],
    pub payload_hash: [u8; 32],
    /// UTF-8 department id, zero padded.
    pub dept: [u8; 16],
    pub required_clearance: u8,
    /// Wallet of the human who signed the action (all zeros if none).
    pub approver: Pubkey,
    pub timestamp: i64,
    pub bump: u8,
}

/// Proof that a document version was approved. Existence of the account for a
/// given hash is the tamper check: recompute SHA-256, derive the PDA, look it up.
/// PDA: ["doc", doc_hash]
#[account]
#[derive(InitSpace)]
pub struct DocRecord {
    pub doc_hash: [u8; 32],
    /// Hash of the version this one superseded (all zeros if none).
    pub parent_hash: [u8; 32],
    pub approver: Pubkey,
    pub entry_index: u64,
    pub timestamp: i64,
    pub bump: u8,
}
