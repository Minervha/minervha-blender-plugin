// Regenerates tests/golden/expected.json from the Studio's mapMaterial.js — the
// single source of truth for the Blender→Wild Life mapping. The committed golden
// is what test_mapper.py asserts against, so the Python port (mapper.py) can never
// silently drift from the JS injector.
//
// Requires the Minervha Studio repo checked out as a sibling folder:
//   ../Minervha Studio/electron-helpers/materialInjector/mapMaterial.js
// (mapMaterial.js is pure — no other Studio files are needed.)
//
// Run from the plugin repo root:  node tests/_gen_golden.cjs

const fs = require('fs');
const path = require('path');

const { mapMaterial } = require('../../Minervha Studio/electron-helpers/materialInjector/mapMaterial.js');

const fixturesPath = path.join(__dirname, 'fixtures', 'normalized_materials.json');
const goldenPath = path.join(__dirname, 'golden', 'expected.json');

const fixtures = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'));
const out = fixtures.map((norm) => {
  const r = mapMaterial(norm);
  return r ? { entry: r.entry, textures: r.textures, report: r.report } : null;
});

fs.writeFileSync(goldenPath, JSON.stringify(out, null, 2) + '\n');
console.log(`wrote golden for ${fixtures.length} fixtures -> ${goldenPath}`);
