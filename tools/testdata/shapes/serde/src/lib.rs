//! Stand-in for the real `serde` crate: only the two trait paths the generator's serde detection reads (`serde::ser::Serialize`,
//! `serde::de::Deserialize`). The fixture crate implements them by hand (no derive).
pub mod ser {
    pub trait Serialize {}
}
pub mod de {
    pub trait Deserialize<'de>: Sized {}
}
pub use de::Deserialize;
pub use ser::Serialize;
