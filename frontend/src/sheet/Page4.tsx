/**
 * Sheet pages 4 and 5: psychic powers.
 *
 * Most characters are not psykers and leave these blank, so on screen the whole section
 * collapses behind a single line until it has something in it. In print it disappears
 * entirely rather than adding two empty pages to every sheet.
 */

import { useState } from "react";

import { NumberField, TextField } from "../components/Field";
import { useSheet } from "../state";
import { PageFooter } from "./PageFooter";

interface MinorPower {
  name: string;
  threshold: number | null;
  focus: string | null;
  sustain: boolean | null;
  isCustom: boolean;
}

export function PsychicPages() {
  const { get, printMode } = useSheet();
  const [expanded, setExpanded] = useState(false);

  const psyRating = get<number | null>("/psychic/psyRating");
  const discipline = get<string | null>("/psychic/psychicDiscipline");
  const minorPowers = get<MinorPower[]>("/psychic/minorPowers") ?? [];
  const powers = get<unknown[]>("/psychic/powers") ?? [];

  const used =
    psyRating != null || Boolean(discipline) || minorPowers.length > 0 || powers.length > 0;

  if (printMode && !used) return null;

  if (!used && !expanded) {
    return (
      <section className="page page--psychic page--collapsed">
        <button type="button" className="page__expand" onClick={() => setExpanded(true)}>
          Psychic Powers <span className="page__hint">(this character is not a psyker — click to add)</span>
        </button>
      </section>
    );
  }

  return (
    <>
      <section className="page page--4">
        <h2 className="block-heading block-heading--page">Psychic Powers</h2>
        <div className="psychic__header">
          <NumberField pointer="/psychic/psyRating" label="Psy Rating" width="3rem" />
          <TextField pointer="/psychic/psychicDiscipline" label="Psychic Discipline" />
        </div>

        <div className="psychic__body">
          <MinorPowerTable />
          <PowerBoxes from={0} count={6} />
        </div>
        <PageFooter />
      </section>

      <section className="page page--5">
        <h2 className="block-heading block-heading--page">Psychic Powers</h2>
        <PowerBoxes from={6} count={12} />
        <PageFooter />
      </section>
    </>
  );
}

/**
 * The 32 pre-printed minor powers, plus write-ins.
 *
 * Threshold, focus and sustain are printed on the form, so they come from the reference
 * data and are never editable for a printed row. Only the tick is the player's.
 */
function MinorPowerTable() {
  const { reference, get, set, printMode } = useSheet();
  const taken = get<MinorPower[]>("/psychic/minorPowers") ?? [];
  const takenByName = new Map(taken.map((power) => [power.name, power]));

  const toggle = (name: string) => {
    const printed = reference.minorPowers.find((power) => power.name === name);
    if (takenByName.has(name)) {
      set(
        "/psychic/minorPowers",
        taken.filter((power) => power.name !== name),
      );
    } else {
      set("/psychic/minorPowers", [
        ...taken,
        {
          name,
          threshold: printed?.threshold ?? null,
          focus: printed?.focus ?? null,
          sustain: printed?.sustain ?? null,
          isCustom: false,
        },
      ]);
    }
  };

  const custom = taken.filter((power) => power.isCustom);
  const printedRows = printMode
    ? reference.minorPowers.filter((power) => takenByName.has(power.name))
    : reference.minorPowers;

  return (
    <div className="minor-powers">
      <h3 className="block-heading">Minor Psychic Powers</h3>
      <table className="minor-powers__table">
        <thead>
          <tr>
            <th aria-label="Known" />
            <th>Name</th>
            <th>Threshold</th>
            <th>Focus</th>
            <th>Sustain</th>
          </tr>
        </thead>
        <tbody>
          {printedRows.map((power) => {
            const on = takenByName.has(power.name);
            return (
              <tr key={power.name} className={on ? "minor-powers__row--known" : ""}>
                <td>
                  {printMode ? (
                    <span className={`tick ${on ? "tick--on" : ""}`} />
                  ) : (
                    <button
                      type="button"
                      className={`tick tick--editable ${on ? "tick--on" : ""}`}
                      aria-label={power.name}
                      aria-pressed={on}
                      onClick={() => toggle(power.name)}
                    />
                  )}
                </td>
                <td>{power.name}</td>
                <td>{power.threshold}</td>
                <td>{power.focus}</td>
                <td>{power.sustain ? "Yes" : "No"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      <CustomMinorPowers custom={custom} all={taken} />
    </div>
  );
}

/** The eight blank lines below the printed table, unbounded here. */
function CustomMinorPowers({ custom, all }: { custom: MinorPower[]; all: MinorPower[] }) {
  const { reference, set, printMode } = useSheet();
  const capacity = reference.printedCapacity["psychic.minorPowers.custom"] ?? 8;

  const replaceCustom = (next: MinorPower[]) =>
    set("/psychic/minorPowers", [...all.filter((power) => !power.isCustom), ...next]);

  const rows = printMode ? custom : [...custom];
  if (!printMode && rows.length < capacity) {
    // Show at least one empty line, so a write-in is always one click away.
    rows.push({ name: "", threshold: null, focus: null, sustain: null, isCustom: true });
  }

  return (
    <table className="minor-powers__table minor-powers__table--custom">
      <tbody>
        {rows.map((power, index) => (
          <tr key={index}>
            <td>
              <span className="tick tick--on" aria-hidden="true" />
            </td>
            <td>
              <input
                type="text"
                className="field__input"
                value={power.name}
                readOnly={printMode}
                onChange={(event) => {
                  const next = [...custom];
                  const updated = { ...power, name: event.target.value };
                  if (index < next.length) next[index] = updated;
                  else next.push(updated);
                  replaceCustom(next.filter((entry) => entry.name.trim()));
                }}
              />
            </td>
            <td>
              <input
                type="number"
                className="field__input field__input--number"
                value={power.threshold ?? ""}
                readOnly={printMode}
                onChange={(event) => {
                  const next = [...custom];
                  if (index < next.length) {
                    next[index] = {
                      ...power,
                      threshold: event.target.value === "" ? null : Number(event.target.value),
                    };
                    replaceCustom(next);
                  }
                }}
              />
            </td>
            <td colSpan={2} />
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** The free-form POWER boxes: six on page 4, twelve on page 5. */
function PowerBoxes({ from, count }: { from: number; count: number }) {
  const { reference, get, printMode } = useSheet();
  const powers = get<unknown[]>("/psychic/powers") ?? [];
  const capacity = reference.printedCapacity["psychic.powers"] ?? 18;

  // Page 4 owns the first six boxes and page 5 the rest; only the last block offers the
  // add button, so a new power lands where there is room for it.
  const isLastBlock = from + count >= capacity;
  const shown = printMode
    ? Math.max(0, Math.min(powers.length - from, count))
    : Math.max(count, isLastBlock ? powers.length - from : 0);

  if (shown <= 0 && printMode) return null;

  return (
    <div className="power-boxes">
      {Array.from({ length: shown }, (_, offset) => from + offset).map((index) => (
        <PowerBox key={index} base={`/psychic/powers/${index}`} index={index} />
      ))}

      {!printMode && isLastBlock && <AddPower powers={powers} />}
    </div>
  );
}

function AddPower({ powers }: { powers: unknown[] }) {
  const { set } = useSheet();
  return (
    <button
      type="button"
      className="repeat__add"
      onClick={() =>
        set("/psychic/powers", [
          ...powers,
          {
            name: null,
            threshold: null,
            focusTime: null,
            sustained: null,
            range: null,
            description: null,
          },
        ])
      }
    >
      + add power
    </button>
  );
}

function PowerBox({ base, index }: { base: string; index: number }) {
  const { get, set, printMode } = useSheet();
  const powers = get<unknown[]>("/psychic/powers") ?? [];
  const exists = index < powers.length;

  // The paper prints empty boxes; on screen they are shown, and filled in on first use.
  const ensure = () => {
    if (exists) return;
    const next = [...powers];
    while (next.length <= index) {
      next.push({
        name: null,
        threshold: null,
        focusTime: null,
        sustained: null,
        range: null,
        description: null,
      });
    }
    set("/psychic/powers", next);
  };

  return (
    <div className="power-box" onFocusCapture={ensure}>
      <TextField pointer={`${base}/name`} label="Power" className="power-box__name" />
      <div className="power-box__row">
        <NumberField pointer={`${base}/threshold`} label="Threshold" width="3rem" />
        <TextField pointer={`${base}/focusTime`} label="Focus Time" />
      </div>
      <div className="power-box__row">
        <TextField pointer={`${base}/sustained`} label="Sustained" />
        <TextField pointer={`${base}/range`} label="Range" />
      </div>
      <TextField
        pointer={`${base}/description`}
        label="Description"
        multiline
        className="power-box__description"
      />
      {!printMode && exists && (
        <button
          type="button"
          className="repeat__remove"
          aria-label={`Remove power ${index + 1}`}
          onClick={() =>
            set(
              "/psychic/powers",
              powers.filter((_, i) => i !== index),
            )
          }
        >
          ×
        </button>
      )}
    </div>
  );
}
