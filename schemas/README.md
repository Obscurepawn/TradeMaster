# Shared schema location

Canonical, packaged language-neutral contracts live in
`crates/tm-core/schemas/`. Hatch includes those exact files in the Python wheel,
and `tm-core` embeds them from inside its Cargo package.
