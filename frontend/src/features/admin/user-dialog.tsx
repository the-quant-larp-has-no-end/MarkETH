"use client";

import { type ReactNode, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Banner, SubmitError } from "@/components/feedback";
import { SegmentedControl } from "@/components/controls";
import { AmountField, Field, revealFirstInvalid, TextAreaField } from "@/components/form";
import { Amount, Missing } from "@/components/money";
import { SheetBody, SheetFooter, sheetHeaderClasses } from "@/components/sheet";
import { DateTime } from "@/components/time";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { type ApiError, messageOf, placeError } from "@/lib/api/errors";
import {
  reinstateUserMutation,
  setUserRoleMutation,
  suspendUserMutation,
} from "@/lib/api/mutations";
import type { AdminUserItem, UserRole } from "@/lib/api/schema";
import type { Attempt } from "@/lib/api/submissions";
import { useSubmissions } from "@/lib/api/submissions";
import { type Submit, useSubmit } from "@/lib/api/submit";
import { formatTokens } from "@/lib/format";
import { useRetryAfter } from "@/lib/hooks";
import { cn } from "@/lib/utils";

import type { DialogFrame } from "./action-dialog";
import {
  ADJUSTED,
  ADJUSTMENTS,
  type Direction,
  NOTHING_REFUSED,
  type SentAdjustment,
} from "./adjustments";
import { roleLine, roleName, ROLES, userStatusName } from "./words";

/** What the dialog shows: the facts of the user, or the step of one action. */
type Step = "facts" | "suspend" | "reinstate" | "role" | "adjust";

/** Who reads a reason: it goes to the audit log, not to the user. */
const AUDIT_HELP = "Kept in the audit log.";

const BOOTSTRAP_NOTE =
  "This admin is named in the server's configuration. The role cannot be changed and the account cannot be suspended here.";

/**
 * Whether an action is offered for a user (DESIGN.md §13). The API decides each action
 * all the same; this is about which buttons the dialog shows.
 */
function offered(step: Step, user: AdminUserItem): boolean {
  switch (step) {
    case "suspend":
      return user.status === "ACTIVE" && !user.bootstrap_admin;
    case "reinstate":
      return user.status === "SUSPENDED";
    case "role":
      return !user.bootstrap_admin;
    case "facts":
    case "adjust":
      return true;
  }
}

/** The role of a user as the table and the dialog say it, with "Bootstrap" where the API says so. */
export function UserRoleText({ user }: { user: AdminUserItem }) {
  return (
    <span className="inline-flex items-center gap-2">
      {roleName(user.role)}
      {user.bootstrap_admin && <Badge variant="muted">Bootstrap</Badge>}
    </span>
  );
}

/** The status of a user: "Active" in plain text, "Suspended" as a badge in the style of "Paused". */
export function UserStatusText({ user }: { user: AdminUserItem }) {
  return user.status === "SUSPENDED" ? (
    <Badge variant="outline">{userStatusName(user.status)}</Badge>
  ) : (
    <>{userStatusName(user.status)}</>
  );
}

/** A label and its value side by side: one fact of the user. */
function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-h-11 items-center justify-between gap-4 border-b py-2 last:border-b-0">
      <dt className="shrink-0 type-body-sm text-muted-foreground">{term}</dt>
      <dd className="min-w-0 text-right type-body-sm wrap-break-word">{children}</dd>
    </div>
  );
}

/** The facts of the row, one under another: how a phone shows all of them. */
function Facts({ user }: { user: AdminUserItem }) {
  return (
    <dl className="flex flex-col">
      <Fact term="Player">
        <span className="block">{user.username}</span>{" "}
        <span className="block type-caption text-muted-foreground">{user.degree_programme}</span>
      </Fact>
      <Fact term="ETH sign-in">
        <span className="block">{user.name === "" ? <Missing /> : user.name}</span>{" "}
        <span className="block type-caption text-muted-foreground">{user.email}</span>
      </Fact>
      <Fact term="Role">
        <UserRoleText user={user} />
      </Fact>
      <Fact term="Status">
        <UserStatusText user={user} />
      </Fact>
      <Fact term="Tokens">
        <Amount value={user.balance_cr} />
      </Fact>
      <Fact term="Joined">
        <DateTime value={user.created_at} />
      </Fact>
    </dl>
  );
}

/**
 * The frame of one step: what it is called, what it asks for, the API's message for a
 * refusal that names none of its fields, then its buttons. The part that asks scrolls;
 * the buttons stay under the line of the footer. The name takes the focus when the step
 * appears, so a screen reader says where the dialog has got to; a step in two parts gives
 * each part a `key`, and each is announced.
 */
function StepForm({
  name,
  error,
  message,
  buttons,
  onSubmit,
  children,
}: {
  name: string;
  /** The failure of the request, and its message when it names none of the fields. */
  error: ApiError | null;
  message: string | null | undefined;
  buttons: ReactNode;
  onSubmit: () => void;
  children: ReactNode;
}) {
  const form = useRef<HTMLFormElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, []);
  // The fields scroll under the buttons: a refused one is brought into view.
  useEffect(() => {
    if (error !== null) revealFirstInvalid(form.current);
  }, [error]);

  return (
    <form
      ref={form}
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      className="flex min-h-0 flex-1 flex-col"
    >
      <SheetBody>
        <h3 ref={heading} tabIndex={-1} className="type-h3">
          {name}
        </h3>
        {children}
      </SheetBody>
      <SheetFooter>
        <SubmitError error={error} message={message} />
        <DialogFooter>{buttons}</DialogFooter>
      </SheetFooter>
    </form>
  );
}

/** The ghost "Back" of a step, which returns to the facts. */
function Back({ disabled, onBack }: { disabled: boolean; onBack: () => void }) {
  return (
    <Button variant="ghost" disabled={disabled} onClick={onBack}>
      Back
    </Button>
  );
}

/**
 * Suspend and Reinstate: the consequence in one or two sentences, and the reason, which
 * is required and goes into the audit log.
 */
function StatusStep({
  name,
  consequence,
  confirm,
  destructive = false,
  action,
  userId,
  onDone,
  onBack,
}: {
  name: string;
  consequence: string;
  confirm: string;
  destructive?: boolean;
  action: Submit<AdminUserItem, { userId: string; reason: string }>;
  userId: string;
  onDone: () => void;
  onBack: () => void;
}) {
  const [reason, setReason] = useState("");
  const placed = action.error ? placeError(action.error, ["reason"]) : null;
  const typed = reason.trim();
  const wait = useRetryAfter(action.error);

  return (
    <StepForm
      name={name}
      error={action.error}
      message={placed?.form}
      onSubmit={() => {
        if (typed === "" || wait > 0) return;
        // A second submit sends nothing while the first is out (DESIGN.md §14).
        action.mutate({ userId, reason: typed }, { onSuccess: onDone });
      }}
      buttons={
        <>
          <Back disabled={action.isPending} onBack={onBack} />
          <Button
            type="submit"
            variant={destructive ? "destructive" : "primary"}
            loading={action.isPending}
            disabled={typed === "" || wait > 0}
          >
            {confirm}
          </Button>
        </>
      }
    >
      <p className="type-body-sm text-muted-foreground">{consequence}</p>
      <TextAreaField
        label="Reason"
        name="reason"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          // An answer is about what was sent: it holds until the input changes.
          if (action.isError) action.reset();
        }}
        help={AUDIT_HELP}
        error={placed?.fields.reason}
      />
    </StepForm>
  );
}

/**
 * Change role: the select, set to the user's role, and one line that follows the choice.
 * The select posts nothing by itself: "Save role" does, once another role is chosen.
 */
function RoleStep({
  user,
  action,
  onDone,
  onBack,
}: {
  user: AdminUserItem;
  action: Submit<AdminUserItem, { userId: string; role: UserRole }>;
  onDone: () => void;
  onBack: () => void;
}) {
  const [role, setRole] = useState<UserRole>(user.role);
  const placed = action.error ? placeError(action.error, ["role"]) : null;
  const wait = useRetryAfter(action.error);
  const same = role === user.role;

  return (
    <StepForm
      name="Change role"
      error={action.error}
      message={placed?.form}
      onSubmit={() => {
        if (same || wait > 0) return;
        action.mutate({ userId: user.id, role }, { onSuccess: onDone });
      }}
      buttons={
        <>
          <Back disabled={action.isPending} onBack={onBack} />
          <Button type="submit" loading={action.isPending} disabled={same || wait > 0}>
            Save role
          </Button>
        </>
      }
    >
      <Field label="Role" help={roleLine(role)} error={placed?.fields.role}>
        {(control) => (
          <NativeSelect
            {...control}
            name="role"
            value={role}
            onChange={(event) => {
              setRole(event.target.value as UserRole);
              if (action.isError) action.reset();
            }}
          >
            {ROLES.map((entry) => (
              <NativeSelectOption key={entry} value={entry}>
                {roleName(entry)}
              </NativeSelectOption>
            ))}
          </NativeSelect>
        )}
      </Field>
    </StepForm>
  );
}

const ADJUSTMENT_FIELDS = ["amount_cr", "reason"] as const;

const DIRECTIONS = [
  { value: "add", label: "Add" },
  { value: "remove", label: "Remove" },
] as const;

/** The adjustment in one sentence, with the amount as it was typed. */
function adjustmentSentence({ direction, amount }: SentAdjustment, username: string): string {
  const tokens = formatTokens(amount, { full: true });
  return direction === "add"
    ? `Add ${tokens} to ${username}?`
    : `Remove ${tokens} from ${username}?`;
}

/**
 * Adjust tokens, in two steps (DESIGN.md §13). The first asks: "Add" or "Remove", an
 * amount and a reason. "Review" leads to the second, which restates the adjustment in one
 * sentence before anything is sent. The confirming tap sends the amount as it was typed,
 * with a minus sign in front of it for "Remove", and the reason.
 *
 * An adjustment that went out and has no verdict (`attempt`) is shown as it was sent, with
 * nothing but "Try again", which sends the stored request once more. A refusal stands on
 * the field it names, back in the first step, or above the button of the second.
 */
function AdjustStep({
  user,
  attempt,
  refusal,
  onConfirm,
  onRetry,
  onEdited,
  onBack,
}: {
  user: AdminUserItem;
  attempt: Attempt<SentAdjustment> | null;
  refusal: ApiError | null;
  /** The confirming tap. `onFieldRefused` is called when the API refuses a field of the first step. */
  onConfirm: (sent: SentAdjustment, onFieldRefused: () => void) => void;
  onRetry: () => void;
  /** The input changed, and a refusal holds only for what was sent. */
  onEdited: () => void;
  onBack: () => void;
}) {
  const [direction, setDirection] = useState<Direction>("add");
  const [amount, setAmount] = useState("");
  const [reason, setReason] = useState("");
  const [reviewing, setReviewing] = useState(false);
  const wait = useRetryAfter(attempt?.error);

  const typedReason = reason.trim();
  const placed = refusal ? placeError(refusal, ADJUSTMENT_FIELDS) : null;
  // A refusal that names a field is about the first step; any other stands above the button.
  const message =
    placed === null || placed.form === null || refusal === null ? null : messageOf(refusal);
  const verb = (of: Direction) => (of === "add" ? "Add tokens" : "Remove tokens");

  if (attempt !== null) {
    const unanswered = attempt.error !== null;
    return (
      <StepForm
        key="kept"
        name="Adjust tokens"
        error={attempt.error}
        message={attempt.error === null ? null : messageOf(attempt.error)}
        onSubmit={() => {
          if (wait === 0) onRetry();
        }}
        buttons={
          <Button type="submit" loading={attempt.sending} disabled={wait > 0}>
            {unanswered ? "Try again" : verb(attempt.direction)}
          </Button>
        }
      >
        <Restated sent={attempt} username={user.username} />
      </StepForm>
    );
  }

  if (reviewing) {
    const sent: SentAdjustment = { direction, amount, reason: typedReason };
    return (
      <StepForm
        key="review"
        name="Adjust tokens"
        error={refusal}
        message={message}
        onSubmit={() => onConfirm(sent, () => setReviewing(false))}
        buttons={
          <>
            <Back
              disabled={false}
              onBack={() => {
                setReviewing(false);
                onEdited();
              }}
            />
            <Button type="submit">{verb(direction)}</Button>
          </>
        }
      >
        <Restated sent={sent} username={user.username} />
      </StepForm>
    );
  }

  const ready = amount !== "" && typedReason !== "";
  return (
    <StepForm
      key="ask"
      name="Adjust tokens"
      error={refusal}
      message={message}
      onSubmit={() => {
        if (!ready) return;
        onEdited();
        setReviewing(true);
      }}
      buttons={
        <>
          <Back disabled={false} onBack={onBack} />
          <Button type="submit" disabled={!ready}>
            Review
          </Button>
        </>
      }
    >
      <SegmentedControl
        label="Add or remove tokens"
        value={direction}
        onChange={(next) => {
          setDirection(next);
          onEdited();
        }}
        segments={DIRECTIONS}
      />
      <AmountField
        label="Amount"
        name="amount_cr"
        value={amount}
        onChange={(next) => {
          setAmount(next);
          onEdited();
        }}
        error={placed?.fields.amount_cr}
      />
      <TextAreaField
        label="Reason"
        name="reason"
        value={reason}
        onChange={(event) => {
          setReason(event.target.value);
          onEdited();
        }}
        help={AUDIT_HELP}
        error={placed?.fields.reason}
      />
    </StepForm>
  );
}

/** The adjustment as one sentence, with its reason under it. */
function Restated({ sent, username }: { sent: SentAdjustment; username: string }) {
  return (
    <div data-slot="adjustment" className="flex flex-col gap-3">
      <p className="type-body wrap-break-word">{adjustmentSentence(sent, username)}</p>
      <dl className="flex flex-col gap-1">
        <dt className="type-caption text-muted-foreground">Reason</dt>
        <dd className="type-body-sm wrap-break-word whitespace-pre-line">{sent.reason}</dd>
      </dl>
    </div>
  );
}

/**
 * The dialog of one user (DESIGN.md §13, Users): titled with the username, it repeats
 * the facts of the row one under another and offers what the admin may do to this user.
 * A tap on an action puts its step in the place of the facts, with a ghost "Back" that
 * returns to them.
 *
 * | Action        | Offered when                              | Sends                        |
 * | ------------- | ----------------------------------------- | ---------------------------- |
 * | Suspend       | the status is ACTIVE, no bootstrap admin  | `.../suspend` with `reason`  |
 * | Reinstate     | the status is SUSPENDED                   | `.../reinstate` with `reason`|
 * | Change role   | no bootstrap admin                        | `.../role` with `role`       |
 * | Adjust tokens | always                                    | `.../adjustments`, kept      |
 *
 * The API alone decides each action. A 2xx closes the dialog and shows a toast; the table
 * behind it shows what the answer says. A refusal shows the API's message in here, on the
 * field it names or above the button, and the dialog stays open. While a request is out
 * the dialog cannot be closed, and while the step of a suspension is shown a tap beside
 * it does not close it either: there it is a confirmation dialog.
 */
export function UserDialog({
  user,
  open,
  onClose,
  onCloseAutoFocus,
}: DialogFrame & {
  /** The user as the table behind the dialog shows them right now. */
  user: AdminUserItem;
}) {
  const [chosen, setChosen] = useState<Step>("facts");
  const suspend = useSubmit(suspendUserMutation);
  const reinstate = useSubmit(reinstateUserMutation);
  const changeRole = useSubmit(setUserRoleMutation);
  const { state: adjustment, store } = useSubmissions(ADJUSTMENTS, user.id);
  const { attempt, refusal } = adjustment;
  /** The action button that opened the step, which takes the focus back after "Back". */
  const from = useRef<Step>("facts");
  const actions = useRef<HTMLDivElement>(null);

  // An adjustment that waits for its verdict is all the dialog shows. A step the user's
  // row does not offer (the list was read again and says so) gives way to the facts.
  const step: Step = attempt !== null ? "adjust" : offered(chosen, user) ? chosen : "facts";
  const pending =
    suspend.isPending || reinstate.isPending || changeRole.isPending || attempt?.sending === true;

  const close = () => {
    if (pending) return;
    // A refusal is about what this opening sent: the next one starts without it.
    store.change(user.id, () => NOTHING_REFUSED);
    onClose();
  };
  const done = (message: string) => () => {
    toast(message);
    onClose();
  };
  const take = (next: Step) => () => {
    from.current = next;
    setChosen(next);
  };
  const back = () => {
    for (const action of [suspend, reinstate, changeRole]) if (action.isError) action.reset();
    store.change(user.id, () => NOTHING_REFUSED);
    setChosen("facts");
  };

  // Back on the facts, the focus returns to the button of the step that was left.
  useEffect(() => {
    if (step !== "facts" || from.current === "facts") return;
    actions.current?.querySelector<HTMLElement>(`[data-step=${from.current}]`)?.focus();
    from.current = "facts";
  }, [step]);

  const action = (next: Step, label: string) =>
    offered(next, user) && (
      <Button variant="secondary" width="full" data-step={next} onClick={take(next)}>
        {label}
      </Button>
    );

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent
        className={cn("gap-0 p-0", sheetHeaderClasses)}
        locked={pending}
        onCloseAutoFocus={onCloseAutoFocus}
        onInteractOutside={(event) => {
          // The step of a suspension is a confirmation: a stray tap neither confirms nor drops it.
          if (step === "suspend") event.preventDefault();
        }}
        aria-describedby={undefined}
      >
        <DialogHeader className="px-4 pt-4 pb-2">
          <DialogTitle className="wrap-break-word">{user.username}</DialogTitle>
        </DialogHeader>

        {step === "facts" && (
          <>
            <SheetBody>
              <Facts user={user} />
              {user.bootstrap_admin && <Banner>{BOOTSTRAP_NOTE}</Banner>}
            </SheetBody>
            <SheetFooter>
              <div ref={actions} data-slot="user-actions" className="flex flex-col gap-2">
                {action("suspend", "Suspend")}
                {action("reinstate", "Reinstate")}
                {action("role", "Change role")}
                {action("adjust", "Adjust tokens")}
              </div>
            </SheetFooter>
          </>
        )}
        {step === "suspend" && (
          <StatusStep
            name="Suspend account"
            consequence={`${user.username} is locked out of MarkETH until an admin reinstates the account. Nothing is taken from it: its tokens, positions and vouchers are kept.`}
            confirm="Suspend"
            destructive
            action={suspend}
            userId={user.id}
            onDone={done("Account suspended")}
            onBack={back}
          />
        )}
        {step === "reinstate" && (
          <StatusStep
            name="Reinstate account"
            consequence={`${user.username} can use MarkETH again.`}
            confirm="Reinstate"
            action={reinstate}
            userId={user.id}
            onDone={done("Account reinstated")}
            onBack={back}
          />
        )}
        {step === "role" && (
          <RoleStep user={user} action={changeRole} onDone={done("Role saved")} onBack={back} />
        )}
        {step === "adjust" && (
          <AdjustStep
            user={user}
            attempt={attempt}
            refusal={refusal}
            onConfirm={(sent, onFieldRefused) => {
              // The confirming tap: one body, one key. While one waits, it sends nothing.
              store.submit(
                user.id,
                {
                  amount_cr: sent.direction === "remove" ? `-${sent.amount}` : sent.amount,
                  reason: sent.reason,
                },
                sent,
                {
                  answered: done(ADJUSTED),
                  refused: (error) => {
                    if (placeError(error, ADJUSTMENT_FIELDS).form === null) onFieldRefused();
                  },
                },
              );
            }}
            onRetry={() => store.retry(user.id, { answered: done(ADJUSTED) })}
            onEdited={() => {
              if (refusal !== null) store.change(user.id, () => NOTHING_REFUSED);
            }}
            onBack={back}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
