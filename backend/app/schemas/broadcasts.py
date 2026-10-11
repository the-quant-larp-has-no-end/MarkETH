"""A market's live video: its broadcast (SPEC §6, §10, Appendix A.2).

On screen it is a "live stream"; in the API, the code and the database it is a
broadcast, because "stream" names the event stream. A market has at most one broadcast
that is open at a time. Its proposer asks for one and an admin decides; an admin starts
one without asking. Whoever is inside the market watches while it is on air.

Picture and sound travel beside these shapes: the studio posts pieces of its recording
to `POST /broadcasts/{id}/chunks`, and a viewer plays `playlist_url`, which the proxy
serves to a caller the API admitted. Of picture and sound nothing is kept.

No shape says how many people watch.
"""

from datetime import datetime
from typing import Annotated, Final
from uuid import UUID

from pydantic import Field

from app.core.enums import BroadcastEndReason, BroadcastStatus
from app.schemas.common import Accepted, RequestModel, ResponseModel
from app.schemas.markets import ShortText

CAPTURE_MIME_TYPES: Final[tuple[str, ...]] = (
    "video/webm;codecs=vp8,opus",
    "video/webm",
    "video/mp4;codecs=avc1.42E01F,mp4a.40.2",
    "video/mp4",
)
"""What a studio may record as, in the order to try: the containers and codecs the
media box takes."""

PART_PATTERN: Final[str] = r"^[A-Za-z0-9_-]{8,36}$"
"""The id a studio gives one run of its recorder, as a regular expression: 8 to 36
letters, digits, hyphens and underscores. A UUID fits it."""

SEQ_LIMIT: Final[int] = 1_000_000_000
"""A piece is numbered from 0 to below this within its part."""

BroadcastPart = Annotated[str, Field(pattern=PART_PATTERN)]
"""The part a piece belongs to."""

BroadcastSeq = Annotated[int, Field(ge=0, lt=SEQ_LIMIT)]
"""The place of a piece in its part."""


class RequestBroadcastRequest(RequestModel):
    """Body of `POST /markets/{id}/broadcast/request`."""

    policy_accepted: Accepted
    """The proposer accepts the usage policy for this live stream, as for a proposal:
    `true`, or the request is rejected on this field."""


class RefuseBroadcastRequest(RequestModel):
    """Body of `POST /admin/broadcasts/{id}/refuse`."""

    reason: ShortText
    """Why, for the person who asked: 1 to `short_text_max_length` characters
    (`GET /config`)."""


class BroadcastChunkQuery(RequestModel):
    """Query string of `POST /broadcasts/{id}/chunks`: which piece the body is."""

    part: BroadcastPart
    """The part the piece belongs to: an id the studio makes whenever its recorder
    starts. The pieces of one part are one recording, cut in the order they were
    made."""

    seq: BroadcastSeq
    """The place of the piece in its part, counted from 0. A piece that is sent again
    carries the same number and the same bytes."""


class BroadcastPerson(ResponseModel):
    """Who asked for a broadcast, as an admin is told."""

    username: str
    email: str


class BroadcastItem(ResponseModel):
    """One broadcast of a market."""

    id: UUID
    market_id: UUID
    status: BroadcastStatus
    """The effective status: a broadcast that ran for `broadcast.max_minutes`, or whose
    studio sent nothing for `broadcast.stall_seconds` (`GET /config`), is ENDED from
    that moment on."""

    on_air: bool
    """It is LIVE and there is something to play."""

    playlist_url: str | None
    """Where its viewers play it, a path on the app's own origin
    (`/api/broadcasts/{id}/hls/index.m3u8`) while it is on air; null otherwise."""

    requested_at: datetime
    """When its proposer asked for it, or when an admin started it."""

    started_at: datetime | None
    ended_at: datetime | None
    end_reason: BroadcastEndReason | None
    """Null until it has ended."""

    refusal_reason: str | None
    """Why an admin refused it. Sent to its broadcaster and to admins, null for
    everyone else and for a broadcast that was not refused."""

    requested_by: BroadcastPerson | None
    """Who broadcasts. Sent to admins only, null for everyone else."""


class BroadcastViewer(ResponseModel):
    """What the caller may do about the live stream of a market right now. Each flag
    agrees with what the operation would answer about this caller and this market;
    how many live streams run at once, and how often a person asks in a day, is told
    by the operation alone."""

    is_broadcaster: bool
    """The broadcast of the answer is the caller's own."""

    can_request: bool
    """`POST /markets/{id}/broadcast/request` would pass: the caller proposed the
    market and is no admin, the market is open, paused or waiting for its result, and
    it has no live stream and no request for one."""

    can_start: bool
    """`POST /broadcasts/{id}/start` would pass: the caller's own broadcast is
    approved, or ended by itself and may go on, and the market is open, paused or
    waiting for its result."""

    can_start_as_admin: bool
    """`POST /admin/markets/{id}/broadcast` would pass: the caller is an admin, the
    market is open, paused or waiting for its result, and it has no live stream and no
    request for one."""

    can_approve: bool
    """`POST /admin/broadcasts/{id}/approve` would pass: the caller is an admin, the
    broadcast waits for a decision, and the market is open, paused or waiting for its
    result."""

    can_refuse: bool
    """`POST /admin/broadcasts/{id}/refuse` would pass: the caller is an admin, and the
    broadcast waits for a decision or is approved and not started."""

    can_end: bool
    """The broadcast is on and the caller is its broadcaster or an admin."""

    can_watch: bool
    """It is on air, and a place is free or the caller holds one."""

    full: bool
    """It is on air, and every place is held by somebody else."""


class MarketBroadcastResponse(ResponseModel):
    """`GET /markets/{id}/broadcast`, and what every action on a broadcast answers."""

    broadcast: BroadcastItem | None
    """The market's latest broadcast, to an admin, to its broadcaster, and to
    everybody inside the market while it is LIVE. Null for everyone else, and for a
    market that never had one."""

    viewer: BroadcastViewer


class BroadcastChunkResponse(ResponseModel):
    """`POST /broadcasts/{id}/chunks`: what became of one piece."""

    restart: bool
    """False: the piece is taken. True: it is not, and the studio begins another
    part with a recorder of its own."""

    next_seq: int
    """The `seq` to send next in this part; 0 with `restart`."""


class BroadcastAccessResponse(ResponseModel):
    """`GET /broadcasts/{id}/access`: the caller may play this broadcast. The proxy
    asks it for every viewer before it serves a playlist or a segment."""

    broadcast_id: UUID


class LiveBroadcastItem(ResponseModel):
    """A broadcast that is on air, with what a list shows of its market."""

    broadcast_id: UUID
    market_id: UUID
    question: str
    started_at: datetime


class LiveBroadcastListResponse(ResponseModel):
    """`GET /broadcasts/live`: the broadcasts that are on air in markets the caller is
    inside of, earliest start first."""

    items: list[LiveBroadcastItem]


class AdminBroadcastItem(BroadcastItem):
    """A broadcast as the admin's list shows it, with the question of its market."""

    question: str


class AdminBroadcastListResponse(ResponseModel):
    """`GET /admin/broadcasts`: every broadcast that is requested, approved or live,
    earliest request first."""

    items: list[AdminBroadcastItem]


class BroadcastConfig(ResponseModel):
    """`broadcast` of `GET /config`: what a studio and a player need to know before
    they start."""

    chunk_ms: int
    """Milliseconds of recording in one piece: what the recorder is started with."""

    chunk_max_bytes: int
    """The most bytes one upload request may carry. A larger piece is cut."""

    upload_backlog_bytes: int
    """Bytes a studio queues while its connection is slow. Beyond them it drops what
    waits and begins another part."""

    capture_height: int
    """The height in pixels the camera is asked for."""

    capture_fps: int
    """The frames per second the camera is asked for."""

    capture_max_width: int
    capture_max_height: int
    """The largest picture, in pixels, a studio records."""

    capture_video_bits_per_second: int
    capture_audio_bits_per_second: int
    """What the recorder is asked to spend on picture and on sound."""

    capture_mime_types: list[str]
    """What a studio may record as, in the order to try. A browser that supports none
    of them cannot broadcast."""

    stall_seconds: int
    """A broadcast whose studio sends nothing for this long has ended."""

    max_minutes: int
    """A broadcast ends once it ran for this long."""

    upload_timeout_ms: int
    """Milliseconds a studio waits for the answer to one piece."""

    upload_retry_ms: int
    """Milliseconds a studio waits before it sends a piece again."""

    restart_window_ms: int
    """Three parts begun within this many milliseconds pause the studio."""

    wait_notice_ms: int
    """Milliseconds without a picture after which a player says that it is waiting
    and reads the broadcast again."""

    player_retry_ms: int
    """Milliseconds after which a player that failed is built again."""
