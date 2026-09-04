/**
 * Sheet page 2: weapons, talents and traits, gear, and the weapon training grid.
 *
 * Every repeating block here is expandable. The printed counts (3 ranged, 4 melee, 21 gear
 * lines) set the starting shape and the point past which export spills onto a continuation
 * page, but nothing caps at them.
 */

import { Checkbox, NumberField, TextField } from "../components/Field";
import { Repeat } from "../components/Repeat";
import { ptr } from "../pointer";
import { useSheet } from "../state";
import { Characteristics } from "./Page1";
import { PageFooter } from "./PageFooter";

export function Page2() {
  const { reference } = useSheet();
  const capacity = reference.printedCapacity;

  return (
    <section className="page page--2">
      {/* The characteristics strip is reprinted on pages 2 and 3 of the real sheet. */}
      <Characteristics />

      <div className="weapons">
        <div className="weapons__column">
          <h2 className="block-heading">Ranged Weapons</h2>
          <Repeat
            pointer="/weapons/ranged"
            printedCapacity={capacity["weapons.ranged"] ?? 3}
            noun="ranged weapon"
            printEmptyRows
            blank={() => ({
              name: null,
              class: null,
              damage: null,
              damageType: null,
              penetration: null,
              range: null,
              rateOfFire: null,
              clip: null,
              reload: null,
              specialRules: [],
            })}
          >
            {(_item, _index, base) => <RangedWeapon base={base} />}
          </Repeat>
        </div>

        <div className="weapons__column">
          <h2 className="block-heading">Melee Weapons</h2>
          <Repeat
            pointer="/weapons/melee"
            printedCapacity={capacity["weapons.melee"] ?? 4}
            noun="melee weapon"
            printEmptyRows
            blank={() => ({
              name: null,
              class: null,
              damage: null,
              damageType: null,
              penetration: null,
              specialRules: [],
            })}
          >
            {(_item, _index, base) => <MeleeWeapon base={base} />}
          </Repeat>
        </div>
      </div>

      <div className="page2__lists">
        <TalentsAndTraits />
        <Gear />
      </div>

      <WeaponTraining />
      <PageFooter />
    </section>
  );
}

// --------------------------------------------------------------------------------------

function RangedWeapon({ base }: { base: string }) {
  return (
    <div className="weapon">
      <TextField pointer={`${base}/name`} label="Name" className="weapon__name" />
      <div className="weapon__stats">
        <TextField pointer={`${base}/class`} label="Class" />
        <TextField pointer={`${base}/damage`} label="Damage" />
        <TextField pointer={`${base}/damageType`} label="Type" width="2.5rem" />
        <NumberField pointer={`${base}/penetration`} label="Pen" width="2.5rem" />
      </div>
      <div className="weapon__stats">
        <TextField pointer={`${base}/range`} label="Range" />
        <TextField pointer={`${base}/rateOfFire`} label="RoF" />
        <NumberField pointer={`${base}/clip`} label="Clip" width="3rem" />
        <TextField pointer={`${base}/reload`} label="Rld" width="3.5rem" />
      </div>
      <SpecialRules pointer={`${base}/specialRules`} />
    </div>
  );
}

function MeleeWeapon({ base }: { base: string }) {
  return (
    <div className="weapon">
      <TextField pointer={`${base}/name`} label="Name" className="weapon__name" />
      <div className="weapon__stats">
        <TextField pointer={`${base}/class`} label="Class" />
        <TextField pointer={`${base}/damage`} label="Damage" />
        <TextField pointer={`${base}/damageType`} label="Type" width="2.5rem" />
        <NumberField pointer={`${base}/penetration`} label="Pen" width="2.5rem" />
      </div>
      <SpecialRules pointer={`${base}/specialRules`} />
    </div>
  );
}

/**
 * The schema stores special rules split into separate strings; the sheet prints them as
 * one comma-separated line. Editing the line and splitting on save keeps both true.
 */
function SpecialRules({ pointer }: { pointer: string }) {
  const { get, set, printMode } = useSheet();
  const rules = get<string[]>(pointer) ?? [];
  const text = rules.join(", ");

  if (printMode) {
    return (
      <span className="printed weapon__rules">
        <span className="printed__label">Special Rules</span>
        <span className="printed__value">{text}</span>
      </span>
    );
  }

  return (
    <span className="field weapon__rules">
      <label className="field__label">Special Rules</label>
      <input
        type="text"
        className="field__input"
        value={text}
        onChange={(event) =>
          set(
            pointer,
            event.target.value
              .split(",")
              .map((rule) => rule.trim())
              .filter(Boolean),
          )
        }
      />
    </span>
  );
}

// --------------------------------------------------------------------------------------

function TalentsAndTraits() {
  const { reference } = useSheet();
  const capacity = reference.printedCapacity;

  return (
    <div className="block block--talents">
      <h2 className="block-heading">Talents and Traits</h2>

      <h3 className="block-subheading">Homeworld / Background</h3>
      <NamedEntryList
        pointer="/talentsAndTraits/homeworldBackground"
        capacity={capacity["talentsAndTraits.homeworldBackground"] ?? 3}
      />

      <h3 className="block-subheading">Advances, Talents and Traits</h3>
      <NamedEntryList
        pointer="/talentsAndTraits/advancesTalentsAndTraits"
        capacity={capacity["talentsAndTraits.advancesTalentsAndTraits"] ?? 15}
      />
    </div>
  );
}

function NamedEntryList({ pointer, capacity }: { pointer: string; capacity: number }) {
  return (
    <Repeat
      pointer={pointer}
      printedCapacity={capacity}
      noun="entry"
      blank={() => ({ name: "", specialisation: null, notes: null })}
      className="named-list"
    >
      {(_item, _index, base) => (
        <div className="named-entry">
          <TextField pointer={`${base}/name`} className="named-entry__name" />
          <TextField
            pointer={`${base}/specialisation`}
            className="named-entry__spec"
            placeholder="(specialisation)"
          />
        </div>
      )}
    </Repeat>
  );
}

function Gear() {
  const { reference } = useSheet();

  return (
    <div className="block block--gear">
      <h2 className="block-heading">Gear</h2>
      <Repeat
        pointer="/gear"
        printedCapacity={reference.printedCapacity["gear"] ?? 21}
        noun="gear line"
        blank={() => ({ name: "", quantity: null, notes: null })}
        className="gear-list"
      >
        {(_item, _index, base) => (
          <div className="gear-entry">
            <NumberField pointer={`${base}/quantity`} className="gear-entry__qty" width="2.5rem" />
            <TextField pointer={`${base}/name`} className="gear-entry__name" />
          </div>
        )}
      </Repeat>
    </div>
  );
}

// --------------------------------------------------------------------------------------

function WeaponTraining() {
  const { reference } = useSheet();
  const { basicAndPistol, melee } = reference.weaponTraining;

  return (
    <div className="block block--training">
      <h2 className="block-heading">Weapon Training Talents</h2>
      <div className="training__columns">
        <div className="training__column">
          {basicAndPistol.map((entry) => (
            <Checkbox
              key={entry.key}
              pointer={ptr("weaponTraining", "basic", entry.key)}
              label={`Basic Weapon Training (${entry.label})`}
            />
          ))}
        </div>

        <div className="training__column">
          {basicAndPistol.map((entry) => (
            <Checkbox
              key={entry.key}
              pointer={ptr("weaponTraining", "pistol", entry.key)}
              label={`Pistol Training (${entry.label})`}
            />
          ))}
        </div>

        <div className="training__column">
          {melee.map((entry) => (
            <Checkbox
              key={entry.key}
              pointer={ptr("weaponTraining", "melee", entry.key)}
              label={`Melee Weapon Training (${entry.label})`}
            />
          ))}

          <Repeat
            pointer="/weaponTraining/exotic"
            printedCapacity={reference.printedCapacity["weaponTraining.exotic"] ?? 4}
            noun="exotic training"
            blank={() => ({ weapon: "" })}
          >
            {(_item, _index, base) => (
              <div className="training__exotic">
                <span className="tick tick--on" aria-hidden="true" />
                <span>Exotic Weapon Training (</span>
                <TextField pointer={`${base}/weapon`} className="training__exotic-name" />
                <span>)</span>
              </div>
            )}
          </Repeat>
        </div>
      </div>
    </div>
  );
}
