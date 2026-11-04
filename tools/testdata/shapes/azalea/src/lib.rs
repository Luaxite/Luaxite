//! Shape fixture (slice 28): ONE case per value shape / feature the generator gained in slices 16-26, on a dependency-free crate (plus the stand-in
//! `serde` next to it) so a real rustdoc can document it: `sh tools/make_rustdoc_json.sh tools/testdata/shapes/azalea tools/testdata/shapes/rustdoc`.
//! Generated with `--config tools/testdata/shapes/bindgen.toml` (every mode on). Nothing here is run: it only has to type-check and be documented.
#![allow(dead_code, unused_variables)]

use std::borrow::Cow;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// The generator wants an `enum Event` (it makes `bot:on(..)` from it); the fixture needs just two variants.
pub enum Event {
    Init,
    Tick,
}

pub struct Client {
    volume: f32,
    name: String,
}

// ---- s18 tuple structs and newtypes
#[derive(Clone, Copy, Debug)]
pub struct Meters(pub f64);
#[derive(Clone, Copy, Debug)]
pub struct Pair(pub i32, pub i32);

// ---- s20 value parameters: a pub-field struct and a data-carrying enum
#[derive(Clone, Debug)]
pub struct Spec {
    pub name: String,
    pub level: u32,
    pub tag: Option<String>,
}
#[derive(Clone, Debug)]
pub enum Shape {
    Dot,
    Circle(f64),
    Line(f64, f64),
}

// ---- s21 serde: private fields, so only the serde facts can map it
#[derive(Clone, Debug)]
pub struct Blob {
    data: Vec<u8>,
}
impl serde::Serialize for Blob {}
impl<'de> serde::Deserialize<'de> for Blob {}

// ---- s22 / s23 / s26: an opaque handle (private fields, Clone) with a static, a `&mut self` method and a getter/setter pair
#[derive(Clone, Debug)]
pub struct Door {
    id: u32,
    level: u32,
}
impl Door {
    pub fn new(id: u32) -> Door {
        Door { id, level: 0 }
    }
    pub fn id(&self) -> u32 {
        self.id
    }
    pub fn bump(&mut self, by: u32) {
        self.level += by;
    }
    pub fn level(&self) -> u32 {
        self.level
    }
    pub fn set_level(&mut self, level: u32) {
        self.level = level;
    }
}

// ---- s24 generic methods
pub trait Marker: Clone {}
#[derive(Clone, Debug)]
pub struct Hunger(pub u32);
#[derive(Clone, Debug)]
pub struct Thirst(pub u32);
impl Marker for Hunger {}
impl Marker for Thirst {}

impl Client {
    // s16 tuples and arrays
    pub fn pos(&self) -> (f64, f64) {
        (0.0, 0.0)
    }
    pub fn set_pos(&self, p: (i32, i32)) {}
    pub fn rgb(&self) -> [u8; 3] {
        [0; 3]
    }
    pub fn set_rgb(&self, c: [u8; 3]) {}

    // s17 references
    pub fn name(&self) -> &str {
        &self.name
    }
    pub fn sum(&self, xs: &[u32]) -> u32 {
        xs.iter().sum()
    }
    pub fn tags(&self) -> &[String] {
        &[]
    }
    pub fn with_opt(&self, o: &Option<u32>) -> u32 {
        o.unwrap_or(0)
    }
    pub fn scale(&self, v: &mut f32) {}

    // s18 tuple structs
    pub fn dist(&self) -> Meters {
        Meters(1.0)
    }
    pub fn set_dist(&self, m: Meters) {}
    pub fn pair(&self) -> Pair {
        Pair(1, 2)
    }
    pub fn set_pair(&self, p: Pair) {}

    // s19 wrappers and std scalars
    pub fn elapsed(&self) -> Duration {
        Duration::ZERO
    }
    pub fn wait_for(&self, d: Duration) {}
    pub fn initial(&self) -> char {
        'x'
    }
    pub fn open(&self, p: &Path) {}
    pub fn home(&self) -> PathBuf {
        PathBuf::new()
    }
    pub fn title(&self) -> Box<str> {
        "t".into()
    }
    pub fn note(&self) -> Cow<'static, str> {
        Cow::Borrowed("n")
    }
    pub fn shared(&self) -> Arc<str> {
        Arc::from("s")
    }
    pub fn locked(&self) -> Arc<Mutex<u32>> {
        Arc::new(Mutex::new(0))
    }
    pub fn rc_name(&self) -> Rc<String> {
        Rc::new(String::new())
    }

    // s20 value parameters
    pub fn apply(&self, s: Spec) {}
    pub fn draw(&self, s: Shape) {}
    pub fn draw_all(&self, shapes: Vec<Shape>) {}

    // s21 serde
    pub fn blob(&self) -> Blob {
        Blob { data: Vec::new() }
    }
    pub fn load(&self, b: Blob) {}

    // s22 handles
    pub fn door(&self) -> Door {
        Door::new(1)
    }
    pub fn open_door(&self, d: Door) {}

    // s24 generic methods
    pub fn component<T: Marker>(&self) -> Option<T> {
        None
    }
    pub fn parse<T: FromStr>(&self, s: &str) -> Option<T> {
        s.parse().ok()
    }

    // s25 callbacks
    pub fn each_tick(&self, f: impl Fn(u32) -> bool) {}
    pub fn for_each(&self, f: impl FnMut(&str)) {}
    pub fn boxed(&self, f: Box<dyn Fn(u32) -> String + Send>) {}
    pub fn later(&self, f: Option<Box<dyn Fn()>>) {}

    // s26 properties: a getter / setter pair on the client itself
    pub fn volume(&self) -> f32 {
        self.volume
    }
    pub fn set_volume(&self, v: f32) {}

    // must stay skipped (coded): a HashMap value with an unmappable element, a returned closure
    pub fn make(&self) -> impl Fn(u32) -> u32 {
        |x| x
    }
    pub fn lookup(&self) -> HashMap<String, Vec<Meters>> {
        HashMap::new()
    }
}
