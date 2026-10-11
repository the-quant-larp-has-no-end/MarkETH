"use client";

import { type ReactNode, useId } from "react";

import { useConfig } from "@/lib/api/session";
import { formatAmount, formatCount, formatTokens, tokenWord } from "@/lib/format";

/** A count with its noun, which follows the number: "1 day", "90 days". */
function counted(count: number, one: string, many: string): string {
  return `${formatCount(count)} ${count === 1 ? one : many}`;
}

/** One section of the policy: a heading in `h3`, 8 px, then its text. */
function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="type-h3">
        {title}
      </h2>
      {children}
    </section>
  );
}

/** A plain bulleted list. */
function Points({ children }: { children: ReactNode }) {
  return <ul className="flex list-disc flex-col gap-2 pl-5">{children}</ul>;
}

/**
 * The usage policy (DESIGN.md §13): the rules of MarkETH as plain text, under nine
 * headings. The words are those of DESIGN.md, which is where VIS changes them.
 *
 * It asks the API for nothing. Its figures are fields of the `/config` that start-up
 * read, and a noun after a figure follows its number. It needs no profile: a visitor
 * reads it before accepting it.
 */
export function PolicyText() {
  const config = useConfig();
  const lifetime = counted(config.max_market_lifetime_days, "day", "days");
  const delay = counted(config.max_resolution_delay_days, "day", "days");
  const proposals = counted(config.max_proposals_per_day, "market", "markets");
  const validity = counted(config.voucher_expiry_days, "day", "days");

  return (
    <div data-slot="policy-text" className="flex flex-col gap-6 type-body text-foreground">
      <Section title="What MarkETH is">
        <p>
          MarkETH is a prediction game for people at ETH Zurich, run by VIS. You trade YES and NO
          shares on questions about campus life. When a market is decided, a share of the outcome
          that came true pays one token and a share of the other outcome pays nothing.
        </p>
        <p>
          Tokens are free. You get {formatAmount(config.signup_grant_cr)} welcome{" "}
          {tokenWord(config.signup_grant_cr)} when you create your profile and{" "}
          {formatAmount(config.monthly_grant_cr)} monthly {tokenWord(config.monthly_grant_cr)} every
          month, counted from that day. VIS also hands out codes, which you redeem for tokens in the
          Wallet.
        </p>
        <p>
          Nothing is sold. Tokens, codes and vouchers cannot be bought, from VIS or from anyone
          else, and none of them can be turned into money. No real money is involved at any point.
        </p>
      </Section>

      <Section title="Markets you may propose">
        <p>
          Anyone with a profile can propose a market. VIS reads every proposal and opens the ones
          that fit this policy. A market you may propose:
        </p>
        <Points>
          <li>
            asks a question with two outcomes, YES and NO, that facts will decide and opinions will
            not;
          </li>
          <li>says what counts as YES and what counts as NO, so that no case is left open;</li>
          <li>
            names its source: a public page, an official announcement or a count that anyone can
            check;
          </li>
          <li>
            has its dates: when trading closes, and by when the result is known. Trading runs for at
            most {lifetime}, and the result has to be known at most {delay} after trading closes.
          </li>
        </Points>
        <p>You can propose {proposals} a day.</p>
      </Section>

      <Section title="Markets you may not propose">
        <p>VIS opens no market about:</p>
        <Points>
          <li>
            the private matters of a person who is named or can be identified: their health, their
            relationships, their money, their conduct;
          </li>
          <li>anyone&apos;s grades or exam results, your own included;</li>
          <li>an outcome that one trader can decide alone, or bring about by what they do;</li>
          <li>party politics, elections and votes;</li>
          <li>religion.</li>
        </Points>
        <p>VIS is a student association within VSETH and takes no side in politics or religion.</p>
      </Section>

      <Section title="Fair play">
        <Points>
          <li>
            One account per person. Your account belongs to your ETH sign-in: do not trade through
            another person&apos;s account, and do not let anyone trade through yours.
          </li>
          <li>You cannot trade a market you proposed.</li>
          <li>
            Do not manipulate an outcome. Do not trade a market whose result you can decide or
            influence, and do nothing to make a market end your way.
          </li>
          <li>
            You can have at most {formatTokens(config.max_stake_per_market_cr)} at stake in one
            market, so that no single trader sets a price.
          </li>
          <li>
            Admins of VIS settle the markets. After trading closes, an admin resolves a market to
            YES or NO from its source and states the evidence, which is shown on the market&apos;s
            page. An admin who traded a market does not settle it.
          </li>
          <li>
            An admin may void a market that cannot be decided fairly, for example when its source
            disappears or its question proves unclear. A voided market pays nobody: every trader
            gets back their net stake, which is what they paid for shares minus what they received
            for selling, and never less than zero.
          </li>
        </Points>
      </Section>

      <Section title="Live chat">
        <p>
          While a market has a live stream, it has a live chat. What you write there is shown at
          once, with your username, to everyone who can see the market. The chat ends with the
          stream, and its messages are deleted then.
        </p>
        <p>An admin of VIS writes in a live chat with the badge VIS.</p>
        <p>In a live chat:</p>
        <Points>
          <li>write about the market;</li>
          <li>do not insult, threaten or harass anyone;</li>
          <li>
            do not post the private matters of a person, advertising, or the same thing again and
            again;
          </li>
          <li>do not try to mislead other traders about an outcome.</li>
        </Points>
        <p>What you write is shown as plain text. Links in it cannot be tapped.</p>
        <p>
          VIS removes messages that break this policy. What is removed disappears for everyone. VIS
          keeps removed messages on record.
        </p>
      </Section>

      <Section title="Vouchers">
        <p>
          Tokens can be turned into a voucher for one product at the bar: a coffee, a drink or a
          snack. The Wallet lists the products and the tokens each one takes.
        </p>
        <p>
          A voucher is a QR code. You show it at the bar, it is scanned, and with that it is used
          up.
        </p>
        <p>
          A voucher is valid for {validity}. One that is not used by then expires, and its tokens
          return to your balance.
        </p>
        <p>Vouchers are not for sale and cannot be exchanged for money.</p>
      </Section>

      <Section title="Live streams">
        <p>
          Whoever proposed a market can ask VIS to stream it live, from a camera. An admin decides
          on every request, and VIS can end a live stream at any moment.
        </p>
        <p>
          A live stream shows what its market is about. Film nobody who has not agreed to it, and
          show nothing this policy keeps out of markets.
        </p>
        <p>
          Viewers see a live stream a few seconds late. No picture and no sound is kept; that a
          stream took place is.
        </p>
      </Section>

      <Section title="What other players can see about you">
        <p>
          Other players see your username, your degree programme and your record on the leaderboard:
          the tokens you hold, the number of your trades, your win rate and your payout ratio. They
          also see what you write in a live chat. If you stream a market live, viewers see and hear
          what your camera shows.
        </p>
        <p>
          They do not see your name or your email address. Both come from your ETH sign-in and are
          shown to you and to the admins of VIS only. They are not shown which markets you trade or
          what you hold in them, and a market does not show who proposed it, unless you stream it
          live yourself.
        </p>
        <p>VIS keeps a record of every movement of tokens and of every action an admin takes.</p>
      </Section>

      <Section title="What VIS may do">
        <p>VIS runs MarkETH and keeps it fair. To do that, VIS may:</p>
        <Points>
          <li>
            reject a proposal, or change its wording and its dates before it opens. You see the
            reason for a rejection;
          </li>
          <li>pause trading in a market, close it early or void it;</li>
          <li>remove a chat message that breaks this policy;</li>
          <li>correct a balance that is wrong, with the reason on record;</li>
          <li>
            suspend an account that breaks this policy. A suspended account is locked out of MarkETH
            until VIS reinstates it;
          </li>
          <li>change this policy. This page shows the policy in force.</li>
        </Points>
      </Section>
    </div>
  );
}
