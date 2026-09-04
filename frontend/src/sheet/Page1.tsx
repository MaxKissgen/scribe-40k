/**
 * Sheet page 1: biography, characteristics, the skills grid, and the condition tracks.
 *
 * The layout follows the printed page closely, because this same markup is what gets
 * rendered to PDF on export. Anything that drifts from the paper here drifts in the
 * printout too.
 */

import { FieldShell, NumberField, TextField } from "../components/Field";
import { PageFooter } from "./PageFooter";
import { padded, Repeat } from "../components/Repeat";
import { ptr } from "../pointer";
import { useSheet } from "../state";
import type { ProficiencyLevel } from "../types";

const ADVANCE_LEVELS: ProficiencyLevel[] = ["Trained", "+10", "+20"];

export function Page1() {
  return (
    <section className="page page--1">
      <BioBlock />
      <Characteristics />
      <SkillsGrid />
      <div className="page1__footer">
        <div className="page1__footer-left">
          <Wounds />
          <FatePoints />
        </div>
        <Armour />
        <div className="page1__footer-right">
          <Insanity />
          <Corruption />
          <Movement />
        </div>
      </div>
      <PageFooter />
    </section>
  );
}

// --------------------------------------------------------------------------------------

function BioBlock() {
  return (
    <div className="bio">
      <TextField pointer="/bio/characterName" label="Character Name" />
      <TextField pointer="/bio/playerName" label="Player Name" />
      <TextField pointer="/bio/career" label="Career" />
      <TextField pointer="/bio/rank" label="Rank" />
      <TextField pointer="/bio/homeWorld" label="Home World" />
      <TextField pointer="/bio/quirk" label="Quirk" />
      <TextField pointer="/bio/divination" label="Divination" />
      <TextField pointer="/bio/ordoOrFaction" label="Ordo (and/or Faction)" />
      <TextField pointer="/bio/description" label="Description" multiline className="bio__wide" />
    </div>
  );
}

// --------------------------------------------------------------------------------------

/**
 * The nine circles.
 *
 * `total` is what the player writes in the circle and the only value they enter. `bonus`
 * is derived server-side and shown read-only, so the two can never disagree.
 */
export function Characteristics() {
  const { reference, get } = useSheet();

  return (
    <div className="characteristics">
      <h2 className="block-heading">Characteristics</h2>
      <div className="characteristics__row">
        {reference.characteristics.map((characteristic) => {
          const base = ptr("characteristics", characteristic.key);
          const bonus = get<number | null>(`${base}/bonus`);
          const advances = get<number | null>(`${base}/advancesTaken`) ?? 0;

          return (
            <div className="characteristic" key={characteristic.key}>
              <div className="characteristic__name">
                {characteristic.label}
                <span className="characteristic__abbrev">({characteristic.abbreviation})</span>
              </div>

              <div className="characteristic__circle">
                <NumberField pointer={`${base}/total`} min={0} max={100} />
                <span className="characteristic__bonus" title="Characteristic bonus, derived">
                  {bonus ?? "—"}
                </span>
              </div>

              <div className="characteristic__advances">
                <span className="characteristic__advances-label">Characteristic Advances</span>
                <div className="tickrow">
                  {[1, 2, 3, 4].map((step) => (
                    <AdvanceTick
                      key={step}
                      pointer={`${base}/advancesTaken`}
                      step={step}
                      taken={advances}
                    />
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

/**
 * One of the four advance boxes.
 *
 * The schema stores a *count*, but the paper shows four boxes, so clicking the third box
 * means "three advances". Clicking the box that is already the highest clears back to one
 * fewer, which is how you undo a misclick.
 */
function AdvanceTick({
  pointer,
  step,
  taken,
}: {
  pointer: string;
  step: number;
  taken: number;
}) {
  const { set, printMode } = useSheet();
  const on = step <= taken;

  if (printMode) return <span className={`tick ${on ? "tick--on" : ""}`} />;

  return (
    <button
      type="button"
      className={`tick tick--editable ${on ? "tick--on" : ""}`}
      aria-label={`${step} advance${step === 1 ? "" : "s"}`}
      aria-pressed={on}
      onClick={() => set(pointer, taken === step ? step - 1 : step)}
    />
  );
}

// --------------------------------------------------------------------------------------

function SkillsGrid() {
  const { reference } = useSheet();
  const columns = [1, 2, 3] as const;

  return (
    <div className="skills">
      <h2 className="block-heading">Skills</h2>
      <div className="skills__columns">
        {columns.map((column) => (
          <div className="skills__column" key={column}>
            <div className="skills__headers">
              {reference.proficiencyColumns.map((header) => (
                <span key={header} className="skills__header">
                  {header}
                </span>
              ))}
            </div>
            {reference.skills
              .filter((skill) => skill.column === column)
              .map((skill) =>
                skill.isGroup ? (
                  <GroupSkillRow key={skill.key} skillKey={skill.key} label={skill.label} />
                ) : (
                  <SkillRow
                    key={skill.key}
                    skillKey={skill.key}
                    label={skill.label}
                    isBasic={skill.isBasic}
                  />
                ),
              )}
          </div>
        ))}
      </div>
      <p className="skills__footnote">† This skill group may encompass multiple skills</p>
    </div>
  );
}

function SkillRow({
  skillKey,
  label,
  isBasic,
}: {
  skillKey: string;
  label: string;
  isBasic: boolean;
}) {
  const pointer = ptr("skills", skillKey, "proficiency", "level");
  return (
    <div className="skill">
      <span className="skill__name">{label}</span>
      <ProficiencyTicks pointer={pointer} isBasic={isBasic} />
    </div>
  );
}

/**
 * The four boxes: Basic, Trained, +10%, +20%.
 *
 * The first is printed information -- filled black for a Basic Skill -- and is not
 * clickable. The other three are the player's marks, and only the rightmost one they have
 * ticked is stored, because that is what the level means.
 */
function ProficiencyTicks({ pointer, isBasic }: { pointer: string; isBasic: boolean }) {
  const { get, set, printMode } = useSheet();
  const level = get<ProficiencyLevel>(pointer) ?? (isBasic ? "Basic" : "Untrained");
  const reached = ADVANCE_LEVELS.indexOf(level);

  // Wrapped in a FieldShell like every other control, so that a flag on a skill can be
  // seen and jumped to. Ticked boxes are most of what a model gets wrong on this sheet,
  // so leaving them outside the review machinery would hide the commonest flag of all.
  return (
    <FieldShell pointer={pointer} className="tickrow tickrow--skill">
      {() => (
        <>
          {/* Printed information: filled black for a Basic Skill, and never clickable. */}
          <span className={`tick tick--printed ${isBasic ? "tick--on" : ""}`} aria-hidden="true" />
          {ADVANCE_LEVELS.map((candidate, index) => {
            const on = reached >= index;
            if (printMode) {
              return <span key={candidate} className={`tick ${on ? "tick--on" : ""}`} />;
            }
            return (
              <button
                key={candidate}
                type="button"
                className={`tick tick--editable ${on ? "tick--on" : ""}`}
                aria-label={candidate}
                aria-pressed={on}
                onClick={() =>
                  set(
                    pointer,
                    // Clicking the highest ticked box steps back down, so a misclick is
                    // undone by clicking the same box again.
                    level === candidate
                      ? index === 0
                        ? isBasic
                          ? "Basic"
                          : "Untrained"
                        : ADVANCE_LEVELS[index - 1]
                      : candidate,
                  )
                }
              />
            );
          })}
        </>
      )}
    </FieldShell>
  );
}

/** A dagger skill: no proficiency of its own, only written-in specialisations. */
function GroupSkillRow({ skillKey, label }: { skillKey: string; label: string }) {
  const { reference } = useSheet();
  const base = ptr("skills", skillKey);
  const examples = reference.groupSkillExamples[skillKey] ?? [];
  const printed = reference.skills.find((skill) => skill.key === skillKey);

  return (
    <div className="skill skill--group">
      <span className="skill__name">
        {label}
        <sup>†</sup>
      </span>

      <Repeat
        pointer={`${base}/specialisations`}
        printedCapacity={Math.max(printed?.writeInLines ?? 2, 1)}
        minimumRows={Math.max(printed?.writeInLines ?? 2, 1)}
        noun="specialisation"
        blank={() => ({ subject: "", proficiency: { level: "Trained" }, notes: null })}
        className="skill__specialisations"
      >
        {(_item, index, itemPointer) => (
          <div className="skill skill__specialisation">
            <TextField
              pointer={`${itemPointer}/subject`}
              placeholder={examples[index] ?? ""}
              className="skill__subject"
            />
            <ProficiencyTicks pointer={`${itemPointer}/proficiency/level`} isBasic={false} />
          </div>
        )}
      </Repeat>
    </div>
  );
}

// --------------------------------------------------------------------------------------

function Wounds() {
  return (
    <div className="block block--wounds">
      <h3 className="block-heading">Wounds</h3>
      <div className="bigbox-row">
        <BigBox label="Total Wounds" pointer="/wounds/totalWounds" />
        <BigBox label="Current Wounds" pointer="/wounds/currentWounds" />
      </div>
      <TextField pointer="/wounds/criticalDamage" label="Critical Damage" />
      <NumberField pointer="/wounds/fatigue" label="Fatigue" width="3rem" />
    </div>
  );
}

function FatePoints() {
  return (
    <div className="block block--fate">
      <h3 className="block-heading">Fate Points</h3>
      <div className="bigbox-row">
        <BigBox label="Total Fate Points" pointer="/fatePoints/totalFatePoints" />
        <BigBox label="Current Fate Points" pointer="/fatePoints/currentFatePoints" />
      </div>
    </div>
  );
}

function BigBox({ label, pointer }: { label: string; pointer: string }) {
  return (
    <div className="bigbox">
      <span className="bigbox__label">{label}</span>
      <NumberField pointer={pointer} className="bigbox__value" />
    </div>
  );
}

/**
 * The armour diagram.
 *
 * The silhouette is the original 1-bit stencil lifted straight out of the template PDF,
 * so this one piece of art is exact rather than redrawn.
 */
function Armour() {
  const { reference } = useSheet();

  const position: Record<string, string> = {
    head: "armour__loc--head",
    rightArm: "armour__loc--right-arm",
    leftArm: "armour__loc--left-arm",
    body: "armour__loc--body",
    rightLeg: "armour__loc--right-leg",
    leftLeg: "armour__loc--left-leg",
  };

  return (
    <div className="block block--armour">
      <h3 className="block-heading">Armour</h3>
      <div className="armour">
        <img
          className="armour__silhouette"
          src="/api/assets/armour-silhouette.png"
          alt=""
          aria-hidden="true"
        />
        {reference.armourLocations.map((location) => (
          <div className={`armour__loc ${position[location.key]}`} key={location.key}>
            <span className="armour__loc-name">{location.location}</span>
            <span className="armour__loc-roll">({location.hitRoll})</span>
            <div className="armour__loc-fields">
              <TextField
                pointer={ptr("armour", location.key, "type")}
                label="Type:"
                className="armour__type"
              />
              <NumberField
                pointer={ptr("armour", location.key, "armourPoints")}
                className="armour__ap"
                width="2.2rem"
              />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function Insanity() {
  const { get } = useSheet();
  const disorders = padded(get<string[]>("/insanity/disorders"), 4, () => "");

  return (
    <div className="block block--insanity">
      <h3 className="block-heading">Insanity</h3>
      <NumberField pointer="/insanity/currentPoints" label="Current Points" width="3rem" />
      <TextField pointer="/insanity/degreeOfMadness" label="Degree of Madness" />
      <StringList pointer="/insanity/disorders" values={disorders} label="Disorders" />
    </div>
  );
}

function Corruption() {
  const { get } = useSheet();
  const malignancies = padded(get<string[]>("/corruption/malignancies"), 4, () => "");

  return (
    <div className="block block--corruption">
      <h3 className="block-heading">Corruption</h3>
      <NumberField pointer="/corruption/currentPoints" label="Current Points" width="3rem" />
      <TextField pointer="/corruption/degreeOfCorruption" label="Degree of Corruption" />
      <StringList pointer="/corruption/malignancies" values={malignancies} label="Malignancies" />
    </div>
  );
}

/** A list of plain strings on printed lines, e.g. Disorders and Malignancies. */
function StringList({
  pointer,
  values,
  label,
}: {
  pointer: string;
  values: string[];
  label: string;
}) {
  const { set, printMode } = useSheet();
  const shown = printMode ? values.filter((value) => value.trim()) : values;

  return (
    <div className="stringlist">
      <span className="field__label">{label}</span>
      {shown.map((value, index) => (
        <input
          key={index}
          type="text"
          className="field__input"
          value={value}
          readOnly={printMode}
          onChange={(event) => {
            const next = [...values];
            next[index] = event.target.value;
            // Trailing blanks are placeholders for the printed lines, not data.
            while (next.length > 0 && !next[next.length - 1].trim()) next.pop();
            set(pointer, next);
          }}
        />
      ))}
      {!printMode && (
        <button
          type="button"
          className="repeat__add"
          onClick={() => set(pointer, [...values.filter((v) => v.trim()), ""])}
        >
          + add line
        </button>
      )}
    </div>
  );
}

/**
 * Movement.
 *
 * These follow from the Agility bonus, but the sheet stores what is written: a player may
 * have a modifier the sheet does not model. A disagreement is flagged, not overwritten.
 */
function Movement() {
  const { get } = useSheet();
  const agilityBonus = get<number | null>("/characteristics/agility/bonus");

  return (
    <div className="block block--movement">
      <h3 className="block-heading">Movement</h3>
      <div className="movement__grid">
        <NumberField pointer="/movement/halfAction" label="Half Action" width="3rem" />
        <NumberField pointer="/movement/fullAction" label="Full Action" width="3rem" />
        <NumberField pointer="/movement/charge" label="Charge" width="3rem" />
        <NumberField pointer="/movement/run" label="Run" width="3rem" />
      </div>
      {agilityBonus != null && (
        <p className="movement__hint screen-only">
          Agility bonus {agilityBonus} suggests {agilityBonus} / {agilityBonus * 2} /{" "}
          {agilityBonus * 3} / {agilityBonus * 6} m
        </p>
      )}
    </div>
  );
}
