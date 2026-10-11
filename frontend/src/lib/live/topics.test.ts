import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";

import { expect, test } from "vitest";

import { MARKET_ID } from "@/test/fixtures";

import { LIVE_EVENT_NAMES } from "./events";
import { broadcastMarketOfTopic, liveTopic, marketOfTopic } from "./topics";

test("the live stream of a market has a topic of its own, named by the market", () => {
  const topic = liveTopic.broadcast(MARKET_ID);

  expect(topic).toBe(`broadcast:${MARKET_ID}`);
  expect(broadcastMarketOfTopic(topic)).toBe(MARKET_ID);
  // It is not the topic of the market: the chat travels on that one, the live stream on this.
  expect(marketOfTopic(topic)).toBeNull();
  expect(broadcastMarketOfTopic(liveTopic.market(MARKET_ID))).toBeNull();
  expect(broadcastMarketOfTopic(liveTopic.markets())).toBeNull();
  expect(broadcastMarketOfTopic(liveTopic.leaderboard())).toBeNull();
});

test("the three events of a live stream stand among the names, after those of main", () => {
  expect(LIVE_EVENT_NAMES.slice(-3)).toEqual(["chat.message", "chat.removed", "broadcast.changed"]);
});

// The contract lies beside the frontend in the repository; an image build has only frontend/.
const CONTRACT = join(process.cwd(), "../backend/openapi.json");

test.skipIf(!existsSync(CONTRACT))("every name is an event the contract's stream sends", () => {
  const contract = readFileSync(CONTRACT, "utf8");

  for (const name of LIVE_EVENT_NAMES) expect(contract, name).toContain(`"const": "${name}"`);
});
