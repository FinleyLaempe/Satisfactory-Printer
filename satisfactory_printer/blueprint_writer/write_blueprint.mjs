// Writes Satisfactory blueprint files (.sbp + .sbpcfg) full of painted beams.
//
//   node write_blueprint.mjs spec.json
//
// spec.json = {
//   template: {sbp, sbpcfg},          // existing blueprint: header/version/config are copied from it.
//                                     // If it contains a beam, that beam object is cloned (safest).
//   out: {sbp, sbpcfg},
//   name, description,
//   designer: 4 | 5 | 6,              // designer size in foundations (Mk1/Mk2/Mk3)
//   beam_type_path, beam_recipe,      // optional overrides
//   swatch,                           // optional swatch path
//   finish,                           // optional paint finish path (e.g. Caterium); stored like
//                                     // the game does: SwatchDesc + OverrideColorData.PaintFinish
//   cost_per_m: [[itemPath, amount_per_metre], ...],
//   beams: [[tx, ty, tz, qx, qy, qz, qw, length_cm], ...]   // Unreal coords, cm
// }
// After writing, the files are parsed again and the beam count is checked.
import { Parser } from '@etothepii/satisfactory-file-parser';
import fs from 'fs';

const spec = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const read = p => { const b = fs.readFileSync(p); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
const clone = o => JSON.parse(JSON.stringify(o));

const BEAM = spec.beam_type_path || '/Game/FactoryGame/Prototype/Buildable/Beams/Build_Beam_Painted.Build_Beam_Painted_C';
const RECIPE = spec.beam_recipe || '/Game/FactoryGame/Prototype/Buildable/Beams/Recipe_Beam_Painted.Recipe_Beam_Painted_C';
const SWATCH = spec.swatch || '/Game/FactoryGame/Buildable/-Shared/Customization/Swatches/SwatchDesc_Slot0.SwatchDesc_Slot0_C';
const className = BEAM.split('.').pop();

const tpl = Parser.ParseBlueprintFiles('template', read(spec.template.sbp), read(spec.template.sbpcfg));
const entities = tpl.objects.filter(o => o.type === 'SaveEntity');
if (!entities.length) throw new Error('template blueprint has no buildings to copy the object layout from');

// 1) best: a real beam from the template; 2) otherwise reshape another building into a beam
let proto = entities.find(o => o.typePath === BEAM && o.properties.mLength);
const fromRealBeam = !!proto;
if (!proto) {
  proto = clone(entities[0]);
  proto.typePath = BEAM;
  const props = {};
  if (proto.properties.BuiltBy) props.BuiltBy = proto.properties.BuiltBy;
  props.mLength = { type: 'FloatProperty', name: 'mLength', propertyTagType: { name: 'FloatProperty', children: [] }, value: 400 };
  const cust = clone(proto.properties.mCustomizationData);
  if (cust?.value?.properties?.SwatchDesc) cust.value.properties.SwatchDesc.value.pathName = SWATCH;
  if (cust) props.mCustomizationData = cust;
  props.mBuiltWithRecipe = { type: 'ObjectProperty', name: 'mBuiltWithRecipe',
    propertyTagType: { name: 'ObjectProperty', children: [] }, value: { levelName: '', pathName: RECIPE } };
  proto.properties = props;
  proto.components = [];
  proto.trailingData = [];
}

if (spec.finish) {
  // layout copied from real buildings with a metal finish (save files, game 1.1+)
  const cust = proto.properties.mCustomizationData || {
    type: 'StructProperty', name: 'mCustomizationData',
    propertyTagType: { name: 'StructProperty', children: [{ name: 'FactoryCustomizationData', children: [{ name: '/Script/FactoryGame', children: [] }] }] },
    value: { type: 'FactoryCustomizationData', properties: {} },
  };
  const ref = { levelName: '', pathName: spec.finish };
  cust.value.properties.SwatchDesc = { type: 'ObjectProperty', name: 'SwatchDesc', propertyTagType: { name: 'ObjectProperty', children: [] }, value: ref };
  cust.value.properties.OverrideColorData = {
    type: 'StructProperty', name: 'OverrideColorData',
    propertyTagType: { name: 'StructProperty', children: [{ name: 'FactoryCustomizationColorSlot', children: [{ name: '/Script/FactoryGame', children: [] }] }] },
    value: { type: 'FactoryCustomizationColorSlot', properties: {
      PaintFinish: { type: 'ObjectProperty', name: 'PaintFinish', propertyTagType: { name: 'ObjectProperty', children: [] }, value: { ...ref } },
    } },
  };
  proto.properties.mCustomizationData = cust;
}

const objects = spec.beams.map((b, i) => {
  const o = clone(proto);
  o.instanceName = `Persistent_Level:PersistentLevel.${className}_${2147000000 - i}`;
  o.transform = {
    rotation: { x: b[3], y: b[4], z: b[5], w: b[6] },
    translation: { x: b[0], y: b[1], z: b[2] },
    scale3d: { x: 1, y: 1, z: 1 },
  };
  o.properties.mLength.value = b[7];
  return o;
});

const totalM = spec.beams.reduce((s, b) => s + b[7] / 100, 0);
const bp = clone(tpl);
bp.name = spec.name;
bp.objects = objects;
bp.header.designerDimension = { x: spec.designer, y: spec.designer, z: spec.designer };
bp.header.recipeReferences = [{ levelName: '', pathName: RECIPE }];
bp.header.itemCosts = (spec.cost_per_m || []).map(([item, perM]) => [{ levelName: '', pathName: item }, Math.max(1, Math.ceil(perM * totalM))]);
bp.config.description = spec.description || '';

let header;
const chunks = [];
const summary = Parser.WriteBlueprintFiles(bp, h => { header = h; }, c => { chunks.push(c); });
fs.writeFileSync(spec.out.sbp, Buffer.concat([Buffer.from(header), ...chunks.map(c => Buffer.from(c))]));
fs.writeFileSync(spec.out.sbpcfg, Buffer.from(summary.configFileBinary));

// verify: read back what we wrote
const check = Parser.ParseBlueprintFiles('check', read(spec.out.sbp), read(spec.out.sbpcfg));
const n = check.objects.filter(o => o.typePath === BEAM).length;
if (n !== spec.beams.length) throw new Error(`verification failed: wrote ${spec.beams.length} beams, read back ${n}`);
if (spec.finish) {
  const f = check.objects[0]?.properties?.mCustomizationData?.value?.properties?.OverrideColorData?.value?.properties?.PaintFinish?.value?.pathName;
  if (f !== spec.finish) throw new Error('verification failed: paint finish not stored');
}
console.log(JSON.stringify({ ok: true, beams: n, fromRealBeam, saveVersion: check.header.saveVersion }));
