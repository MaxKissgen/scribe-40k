/**
 * Sheet page 3: the rank advance blocks and experience totals.
 *
 * The paper prints eight rank blocks of twelve rows each, plus Elite Advances. Most sheets
 * use two or three, so on screen a rank block collapses to its heading until it has
 * something in it -- otherwise 96 empty rows bury the ones that matter.
 */

import { useState } from "react";

import { NumberField, TextField } from "../components/Field";
import { Repeat } from "../components/Repeat";
import { useSheet } from "../state";
import { Characteristics } from "./Page1";
import { PageFooter } from "./PageFooter";

interface AdvanceEntry {
  advance: string | null;
  cost: number | null;
}

interface RankBlock {
  rank: number;
  entries: AdvanceEntry[];
}

export function Page3() {
  const { reference, get } = useSheet();
  const blocks = get<RankBlock[]>("/advances/rankAdvances") ?? [];

  return (
    <section className="page page--3">
      <Characteristics />

      <div className="experience">
        <div className="bigbox-row">
          <div className="bigbox">
            <span className="bigbox__label">Total Experience</span>
            <NumberField pointer="/advances/totalExperience" className="bigbox__value" />
          </div>
          <div className="bigbox">
            <span className="bigbox__label">Spent Experience</span>
            <NumberField pointer="/advances/spentExperience" className="bigbox__value" />
          </div>
        </div>
      </div>

      <div className="advances">
        {Array.from({ length: reference.printedRankBlocks }, (_, index) => index + 1).map(
          (rank) => (
            <RankAdvances key={rank} rank={rank} blocks={blocks} />
          ),
        )}

        <div className="advance-block">
          <h3 className="block-heading">Elite Advances</h3>
          <AdvanceRows
            pointer="/advances/eliteAdvances"
            capacity={reference.printedCapacity["advances.eliteAdvances"] ?? 12}
          />
        </div>
      </div>
      <PageFooter />
    </section>
  );
}

/**
 * One rank block.
 *
 * The schema keys these by rank rather than by position, so a character with advances at
 * ranks 1 and 3 stores exactly those two. The UI has to find or create the right entry
 * rather than index into a fixed array.
 */
function RankAdvances({ rank, blocks }: { rank: number; blocks: RankBlock[] }) {
  const { set, printMode, reference } = useSheet();
  const index = blocks.findIndex((block) => block.rank === rank);
  const entries = index >= 0 ? blocks[index].entries : [];
  const [expanded, setExpanded] = useState(false);

  const used = entries.some((entry) => entry.advance || entry.cost !== null);

  // On paper every rank block is printed, empty ones included, so there is somewhere to
  // write the next advance. Print does the same. It also keeps page 3's layout the same
  // shape regardless of how far the character has got, which is what lets an exported
  // sheet be recognised again on re-import.
  if (!printMode && !used && !expanded) {
    return (
      <div className="advance-block advance-block--collapsed">
        <button type="button" className="advance-block__expand" onClick={() => setExpanded(true)}>
          Rank {rank} Advances <span className="advance-block__hint">(empty — click to fill)</span>
        </button>
      </div>
    );
  }

  const ensureBlock = () => {
    if (index >= 0) return;
    set("/advances/rankAdvances", [...blocks, { rank, entries: [] }]);
  };

  return (
    <div className="advance-block">
      <h3 className="block-heading">Rank {rank} Advances</h3>
      <div onFocusCapture={ensureBlock}>
        <AdvanceRows
          pointer={
            index >= 0
              ? `/advances/rankAdvances/${index}/entries`
              : `/advances/rankAdvances/${blocks.length}/entries`
          }
          capacity={reference.printedCapacity["advances.rankAdvances.entries"] ?? 12}
          onBeforeAdd={ensureBlock}
        />
      </div>
    </div>
  );
}

function AdvanceRows({
  pointer,
  capacity,
  onBeforeAdd,
}: {
  pointer: string;
  capacity: number;
  onBeforeAdd?: () => void;
}) {
  return (
    <div onClick={onBeforeAdd}>
      <Repeat
        pointer={pointer}
        printedCapacity={capacity}
        minimumRows={6}
        printEmptyRows
        noun="advance"
        blank={() => ({ advance: null, cost: null })}
        className="advance-rows"
      >
        {(_item, _index, base) => (
          <div className="advance-row">
            <TextField pointer={`${base}/advance`} className="advance-row__name" />
            <NumberField pointer={`${base}/cost`} className="advance-row__cost" width="3.5rem" />
          </div>
        )}
      </Repeat>
    </div>
  );
}
