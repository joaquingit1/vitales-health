# Deployment

How Crew Health Check is deployed on a spacecraft, a station, a surface base, a rover and on Earth. Binding for every
epic design (section 9 of each TDD names these tiers) and for E20 (packaging and resilience). The software architecture
that runs on these nodes is in `docs/SYSTEM.md`; canonical requests live in `docs/requests/SYSTEM.md` with
the IDs `R-SYS-n` used below. Part A of COORDINATOR-DECISIONS.md is binding; A3 indexes superseded package lines.

Evidence base, cited by file and section: the build research artifact `crew-health-build-2026-11-14/research/`
(`vehicle-computing.md`, `surface-mobility-comms.md`, `medical-systems-standards.md`, `health-data-standards.md`,
`software-regulatory-standards.md`). Source tags in brackets (for example [VC18]) are the research files' own source IDs.
Where we choose differently from what the evidence shows NASA doing, the text says why.

## 1. What the evidence fixes

1. **Never on flight computers.** Orion's flight computers run partitioned, certified flight software and ISS PCS
   laptops are criticality 1 on the command bus; crew support software runs on criticality 3 laptops off the command bus
   (vehicle-computing.md 3.1, items 1 and 2 [VC11] [VC12] [VC14] [VC30]). The hub is crew support software and never
   asks to run on a flight computer or to write to vehicle systems.
2. **The minimum setup is the realistic flight configuration.** The ISS Medical Equipment Computer (MEC) was one laptop
   with a medical software load, device interfaces and a store and forward path [VC18]. An Orion class capsule has no
   crew laptop or server, only four Surface Pro tablets with webcams that already carry private medical conferences
   [VC25] (vehicle-computing.md 3.1 items 3 and 5). One laptop and its webcam is therefore tier T0 and is complete.
3. **Windows, macOS and Linux.** The ISS station support computers are HP ZBook Windows laptops and the Orion crew
   devices are Surface Pro tablets (vehicle-computing.md 1.1, 1.2, 3.2 [VC03] [VC05] [VC25]). The hub, the workers and
   the crew app run on all three operating systems, and the crew app runs in desktop browsers and in iPadOS Safari
   with nothing loaded from the internet (3.2).
4. **Every place where a crew sleeps or works for hours is a full hub**; suits and the unpressurized Lunar Terrain
   Vehicle are data sources, not nodes; the ground is one more replica reachable only through the outbox
   (surface-mobility-comms.md 7.1, drivers [N07] [N08] [N10] [SM24]).
5. **Faults are routine.** COTS laptops on ISS showed 0.013 radiation functional interrupts per day (about one per 77
   laptop days), plus ordinary faults, about one interrupt every few weeks per laptop, and two of seven T61Ps failed hard
   (vehicle-computing.md 2.4 and 3.5 [VC17]). EXPRESS laptops stored crash data for downlink and were re-imaged from a
   golden copy [VC15]; Ingenuity kept a read-only golden copy for abrupt power loss [VC51]. Crash safety, a watchdog,
   crash logs, an integrity check at start, a portable database and a one command golden restore are requirements.
6. **The link is narrow, shared and often absent.** ISS Ku-band covers about 85% of the time; Orion S-band is limited
   and shared with about 40 minutes behind the Moon; the lunar South Pole sees Earth 17% to 43% of the time; Mars is 4
   to 24 minutes one way with a 2 to 3 week conjunction (vehicle-computing.md 3.6; surface-mobility-comms.md 2.1 to 2.4).
   Nothing on board waits for the ground (package USE-CASES rule 7).
7. **Store and forward is how medical data already moves**, and DTN BPv7 is operational in NASA's Near Space and Deep
   Space Networks and mandatory in LunaNet (vehicle-computing.md 3.6 [VC10] [RT19]; surface-mobility-comms.md 2.5
   [SM01] [SM02] [SM03]).
8. **The model is optional and expensive.** The 9B model fits an SSC class ZBook G9 (128 GB, RTX GPU), Orion PCD
   capability is not public, and no laptop power budget is published, so the model loads on demand and every flow works
   with it off (vehicle-computing.md 3.7 [VC03]).

## 2. Node roles

| Role | What it is | Runs |
|---|---|---|
| Hub node | The machine that holds a site's records | `crew-health` supervisor, hub, and the workers that site has |
| Client device | A browser on a crew laptop, a station display, a tablet or a phone | The crew app (offline web app); never the record of truth |
| Worker | A separate process on a hub node or on the device that holds a camera | Camera worker, device adapters, model server, embedding server, forecast worker, ground link agent, DTN daemon |
| Integration node | A vehicle or base computer that already sees habitat telemetry | A habitat adapter that pushes readings to the hub; nothing of ours runs on flight computers |
| Ground node | A hub in the `ground` profile on Earth | The ground console (Open MCT), bundle ingest and the ground replica |

A **site** is one habitable volume with one hub: a station segment, a capsule, a transit vehicle, a surface habitat or
a pressurized rover. Each hub node has a `node_id` (settings, section 6.2), a node key pair for signing bundles and sync
files, and its own SQLite files.

## 3. Tiers

Each tier adds to the one before. Every tier keeps every safety function of T0 with the model stopped, every device
absent and the ground link down (charter section 1, rule 2).

| Tier | Name | Adds | Required for the tier |
|---|---|---|---|
| T0 | Minimum: one laptop and its webcam | Everything that is required | The laptop |
| T1 | Crew laptops and a station display | More browsers on the same hub | A LAN between them |
| T2 | Tablets and phones | Mobile browsers on the habitat network | Enrollment (section 5.2) |
| T3 | Medical kit devices | BLE oximeter, cuff, thermometer, band | A BLE radio on a node running the adapter |
| T4 | Habitat environmental feeds | LIVE habitat channels; REPLAY of NASA telemetry and SIM | A feed or a recorded file |
| T5 | Vehicle or base integration node | Telemetry from the vehicle's own computers | A read-only feed from the vehicle |
| T6 | Several hubs | A pressurized rover, a second module, a transit replica | A second hub node; sync (section 4) |
| T7 | Ground link and ground node | Store and forward bundles to Earth and back | A transport: removable media, a DTN node or HTTPS on Earth |

### 3.1 T0 Minimum: one laptop and its webcam

| Aspect | Decision |
|---|---|
| Hub | The laptop runs `uv run crew-health` (native, no containers). The hub binds `127.0.0.1:8080` only (CONTRACTS section 10: localhost by default) and serves the built crew app on the same origin |
| Clients | The laptop's browser. At rest it shows a `station` session (no person: habitat view, broadcasts, help entry). A crew member signs in with a PIN or passphrase for personal work; the idle lock returns to the station session (answers E26 Q9) |
| Workers | Camera worker on the same laptop reading the built in webcam. Model server and forecast worker only when enabled in settings and resources allow (section 7.3) |
| Data | All records in `<data_dir>/hub.sqlite`; private entries (journal, private reports) in `<data_dir>/private.sqlite`, encrypted per entry with owner access (section 5.4); rolling backups in `<data_dir>/backups/`; outbox bundles in `<data_dir>/outbox/`; the golden copy on a separate volume or removable medium (section 7.5). Raw camera frames are never stored (CONTRACTS section 10) |
| Failure | The hub process dies: the supervisor restarts it (section 7.2); the open browser keeps the cached first help screen and active plan steps and queues commands (E26, E28). The laptop dies: restore the last backup on any other laptop with `crew-health restore` (portable database, vehicle-computing.md 3.5) and use the printed help and plan cards until then (`crew-health help print-cards`, E26; plan print view, E28) |
| Power and compute | Target the reference laptop of PRD section 10 (Apple M3, 16 GB). Hub and camera stay CPU light; the model is loaded on demand and unloaded when idle (vehicle-computing.md 3.7). Budgets in SYSTEM.md section 3 |
| Required | Laptop, browser, webcam for camera checks (manual entry when absent) |
| Optional | Everything else |

The minimum laptop is a crew device and a station at once: help opens from the station session without sign-in (PRD.md
line 548: "The fixed first screens of Someone needs help stay usable before authentication is complete"); the
subject's readings appear only after the helper signs in.

### 3.2 T1 Crew laptops and a station display

| Aspect | Decision |
|---|---|
| Hub | One hub per site on a designated laptop or small server: the SSC laptop or server on the Joint Station LAN on ISS class stations (the MEC and HDTN gateway precedents [VC18] [VC10]); the support server with laptops as clients on a Gateway class or surface habitat [VC38] [VC46] (vehicle-computing.md 3.1) |
| Clients | Each crew laptop runs the crew app in a browser against the site hub; a station display (a laptop or wall screen on a `station` session) shows habitat status, broadcasts and the help entry |
| Network | Plain IP over Ethernet or Wi-Fi; no multicast discovery: the hub address is configured explicitly (vehicle-computing.md 3.3). The hub opens a second listener `https://<hub address>:8443` only when `network.lan.enabled` is set, with TLS 1.3 and a certificate from the hub's local CA (CONTRACTS section 10: LAN exposure needs authentication, encryption and explicit enabling) |
| Data | Only on the hub. Clients hold the offline cache of SYSTEM.md section 8.3 (configuration by version, first help screen, active plan steps, the signed-in person's queued commands) |
| Failure | A client dies: nothing is lost that the hub accepted. The hub laptop dies: any crew laptop becomes the hub by restoring the last backup (portable database); the station display falls back to its cached first help screen |
| Power and compute | The hub machine carries the model if any machine does; crew laptops run only a browser |
| Required | The hub node and one browser |
| Optional | More laptops, the station display |

### 3.3 T2 Tablets and phones on the habitat network

| Aspect | Decision |
|---|---|
| Hub | Unchanged (site hub) |
| Clients | Windows tablets (Orion PCDs, ISS Surface Pros) and iPads [VC24] [VC05] [VC25] run the crew app in the browser. Personal phones are optional clients only if a crew member chooses; never a data source the system depends on [VC27] (vehicle-computing.md 3.2) |
| Network | WPA2 minimum, WPA3-Enterprise preferred (ICSIS, vehicle-computing.md 3.3 [VC40]). Each device is enrolled before it can reach the LAN listener (section 5.2) |
| Cameras | A tablet camera can serve as a camera only through a camera worker running on that device; frames never leave the capturing device (charter section 2). A browser alone cannot host the camera worker, so a tablet without the worker is not a reference camera (answers E30 X17) |
| Data | As T1. A tablet carried away from the site keeps only its cache and queue and syncs on return (answers E26 Q9: away devices sync on the habitat network when back in range, never through another site's hub) |
| Failure | A lost or stolen tablet: section 5.5 |
| Required | Enrollment and the LAN listener |
| Optional | Every tablet and phone |

On an Orion class capsule the hub and the browser run together on one PCD, T0 shape on Windows; multi device sync over
the cabin Wi-Fi is possible in principle but not confirmed for PCDs (vehicle-computing.md 3.1 item 3), so it is not
assumed.

### 3.4 T3 Medical kit devices over Bluetooth

| Aspect | Decision |
|---|---|
| Hub | Unchanged |
| Adapters | One adapter process per device family (E07 oximeter, cuff, thermometer; E08 band) on the node that has the BLE radio, normally the hub laptop. `bleak` with the Bluetooth SIG profiles: Pulse Oximeter 0x1822, Blood Pressure 0x1810, Health Thermometer 0x1809, Heart Rate 0x180D (health-data-standards.md 4; medical-systems-standards.md 4.5). Bluetooth 4.2 and BLE are in ICSIS [VC40] |
| Path | The adapter posts readings and attempts with its per adapter token (`/v1/adapters/{adapter_id}/readings`, `/v1/measurement-requests/{request_id}/attempts`); device clocks are untrusted and the hub stamps reception time (the PHD IG gateway rule, health-data-standards.md 1, PHD IG) |
| Data | Readings on the hub only; adapters keep a bounded send buffer with their sequence numbers and resend after a hub restart (`(channel_id, adapter_sequence)` is unique, CONTRACTS section 13) |
| Failure | Device absent or adapter dead: manual entry for every device; the kit map counts change live (charter section 5) |
| Required | Nothing; manual entry is always available |
| Optional | Each device and its adapter |

Legacy RS-232 and infrared medical devices existed on ISS [VC18]; the adapter layer stays modular as NASA's MDA was
[VC19] (vehicle-computing.md 3.3).

### 3.5 T4 Habitat environmental feeds

| Aspect | Decision |
|---|---|
| How real vehicles expose them | Through flight software: cFS on Gateway, CCSDS packets or XTCE described telemetry; NASA's MDA prototypes moved medical and environmental data over core Flight Software on a simulated vehicle network (vehicle-computing.md 3.3 [VC41] [VC43] [VC22]; surface-mobility-comms.md 3.5 [SM39]). ISS environmental sensors reach laptops over USB and Bluetooth (surface-mobility-comms.md 7.5 [SM28] [SM29]) |
| Adapters (E23, `adapters/habitat`) | LIVE: an XTCE or CCSDS packet reader and a Bluetooth Environmental Sensing (0x181A) reader, both behind the same ingest port. REPLAY: recorded NASA telemetry, such as the RR-1 CO2 file and DosTel1, replayed with their original dates. SIM: generated data for drills and the demo. Every reading carries `origin_mode` to the screen (AGENTS.md rule 2) |
| Data | Readings and evaluations on the hub of the site where the sensor is. Alerts are raised by that hub (surface-mobility-comms.md 7.4 item 5) |
| Failure | Feed silent: `sensor_quiet` at watch, stale and never reported states, never shown as current; the vehicle's own caution and warning system stays the authority (E24 section 9) |
| Required | Nothing; manual readings, REPLAY or SIM, each labeled |
| Optional | Every feed |

### 3.6 T5 Vehicle or base integration node

The hub never runs on a flight computer and never writes to one. Where a vehicle exposes telemetry, an integration
node (a payload laptop or the habitat's data system gateway, as HDTN sat on the JSL [VC10]) runs the LIVE habitat
adapter, reads the feed one way and pushes readings to the site hub with an adapter token. Vehicle alarms reach the
hub as mission events recorded by the crew or by that adapter (E24 request R-E24-9), never by inference. Failure of
the integration node is a quiet feed (T4). Nothing in the minimum setup needs it. We cannot claim to read Orion,
Gateway, HLS or LTV sensors today: their interfaces are not public (vehicle-computing.md 3.9;
surface-mobility-comms.md 7.7).

### 3.7 T6 Several hubs: rover, second module, transit replica, EVA

| Aspect | Decision |
|---|---|
| Hubs | One hub per site (section 4.1). A pressurized rover carries a laptop hub with its own SQLite, rules, alerts and cached plans (surface-mobility-comms.md 7.1, 7.5). A transit vehicle may carry a medical station hub plus a crew laptop replica (7.5 table) |
| EVA | Not a node. Suit data reaches the host hub through an adapter live if exposed or as a post EVA import; nothing is pushed to the suited crew, and the IV crewmember owns messages and suit data (surface-mobility-comms.md 4 and 7.6 [SM26] [SM35]). The LTV is not a node either; an LTV sortie's devices are clients of the hub they return to |
| Data | Each hub holds the records it created plus authorized projections from its peers (section 4). A rover sortie carries its own kit and supplies as a custody location (SYSTEM.md section 8.5) |
| Failure | Hubs out of contact keep working fully; nothing waits on a peer; each raises its own alerts. A rover hub that dies is restored from its last backup or its records are recovered from the base on return up to the last sync |
| Power and compute | Battery cycles (8 h driving, 16 h charging per Earth day in the JAXA concept [SM24]); start in seconds and recover after power loss (WAL, integrity check). The rover hub runs without the model by default |
| Required | The second hub node |
| Optional | The whole tier; the walking skeleton and T0 to T5 never need it |

Multi-node demonstration without new hardware: two hub processes on the same laptop with separate data directories
and node IDs, labeled Habitat and Rover, and a link toggle between them (surface-mobility-comms.md 7.5).

### 3.8 T7 Ground link and ground node

| Aspect | Decision |
|---|---|
| Flight side | The ground link module (E29) writes bundle shaped files to `<data_dir>/outbox/` (section 4.6). The ground link agent process hands them to the configured transport and drops inbound bundles into `<data_dir>/inbox/`. The hub never waits for delivery |
| Transports | (a) File drop: a folder on removable media or a shared drive, the default and the minimal one; (b) a local DTN node: µD3TN through `pyd3tn` (best fit: small daemon, Python application agent API, BPv7, POSIX) or dtn7-rs over its HTTP convergence layer (MIT or Apache-2.0, cross platform) as fallback; ION on Linux where a program already runs it; never HDTN, archived on 3 November 2026 (surface-mobility-comms.md 2.5 and 7.3 [SM07] [SM08] [SM09] [SM10]); (c) HTTPS between the ground node and mission systems on Earth only |
| Ground node | A hub in the `ground` profile on Earth that ingests bundles into a read replica and serves the ground console (Open MCT, E16) and `/v1/ground/*` operations. Ground sessions exist only there in a flight deployment; the single laptop demo enables the ground console on the flight hub behind the operator's demo switch (`/v1/demo/ground-link`) |
| Data | Onboard is the record; the ground copy is a reader (vehicle-computing.md 3.6, the Playbook rule). Ground receipts and replies are new records sent back as bundles; they never edit onboard records (surface-mobility-comms.md 7.4 item 4) |
| Failure | Link down: everything works; the queue size and last contact are shown (charter section 5). Ground node down: bundles wait in the transport or on media until lifetime, then the local copies stay |
| Required | Nothing |
| Optional | The whole tier |

### 3.9 Reference deployments per environment

| Environment | Hub node | Clients | Tiers used | Ground link |
|---|---|---|---|---|
| Station (ISS class) | SSC laptop or SSC server on the station LAN | Crew laptops, tablets, station display | T0 to T5, T7 | Near continuous (about 85% Ku coverage [VC09]); same outbox |
| Capsule (Orion class) | One Surface Pro PCD, Windows | The same PCD | T0, T3 | Limited shared S-band, about 40 minutes behind the Moon [VC25] [VC33] |
| Transit vehicle | Medical station computer plus a crew laptop replica | Crew tablets | T0 to T4, T6, T7 | Delay grows to about 22 minutes; Mars conjunction |
| Moon base | Habitat laptop or support server | Laptops, tablets, station display | T0 to T7 | 17% to 43% direct to Earth at the pole until relays [N08] |
| Mars base | Habitat laptop plus a vehicle replica | As Moon base | T0 to T7 | 4 to 24 minutes; 2 to 3 week conjunction [N09] [N10] |
| Pressurized rover | Laptop hub | Crew tablets | T0, T3, T4, T6 | Usually through the base; stores locally [SM24] |
| LTV and EVA | None | Suit and LTV data through the host hub's adapters | none | Through the host hub |

Sources: vehicle-computing.md 3.1; surface-mobility-comms.md 7.5.

## 4. Multi-node topology and sync

### 4.1 Decision: one authoritative hub per site; sites replicate an append-only log

**Decision.** Each site has exactly one hub, authoritative for every object created there. Sites exchange records by
anti-entropy over an append-only per-node log. There is no consensus protocol, no shared master and no ground
authority. Every mutable object has one writer, its home node; other nodes hold read-only replicas and may add new
records that refer to it (proposals, notes, observations).

**Evidence and alternatives.**

| Option | Verdict | Why |
|---|---|---|
| One central hub with thin clients everywhere | Rejected | A rover out of range or a module on battery would lose every function; out of range vehicles store data and send it "as bandwidth allows" [SM24]; alerts must be raised locally by the node with the sensor (surface-mobility-comms.md 7.4 item 5, [V2 6022] [V2 6164]) |
| Multi-master editing of every record (general CRDTs) | Rejected | Clinical content must never merge automatically; two versions are shown and a medical officer chooses (surface-mobility-comms.md 7.4 item 3, extending PRD R11). Single writer per object makes that rule cheap and testable |
| Ground as the authority | Rejected | The ground is never a step (USE-CASES rule 7, EI1); 5 minute delays behave like no contact [SM35] |
| One hub per site plus append-only replication | Chosen | The CAST pattern of periodic flat-file exchange with a written conflict rule "the crew changes would win" [SM37]; the Playbook sync pattern (vehicle-computing.md 3.6 [VC23]); works over LAN, removable media and the ground outbox with one format (surface-mobility-comms.md 7.2) |

### 4.2 Identifiers and the replication log

- **Node ID**: `node_id` matches the contract `id` pattern, for example `hab-1`, `rover-1`, `ground-1`; unique per
  mission and installation. A separate random `origin_incarnation` is generated at first install and every restore,
  is never reused, and is persisted before writes. Site identity is stable; write incarnation is not.
- **Record IDs**: every ID the hub generates is `<prefix>_<uuid7 hex>` (RFC 9562 UUIDv7 from Python 3.14's
  `uuid.uuid7()`), globally unique without coordination. IDs the contracts call deterministic (routine occurrences) are
  derived (SYSTEM.md section 5.1) and are equal on every node by design.
- **Log entry** (internal, table `repl_log` owned by E01 platform):

| Field | Meaning |
|---|---|
| `origin_node_id` | Node that created the entry |
| `origin_incarnation` | Unique write epoch of that node; new after restore |
| `origin_seq` | Gap-free committed sequence within `(origin_node_id, origin_incarnation)` |
| `hlc` | Hybrid logical clock `(physical_ms, logical, origin_node_id, origin_incarnation)`; orders entries across nodes, never the wall clock alone (surface-mobility-comms.md 7.2 item 5) |
| `record_type` | One of the replicated types of 4.4 |
| `record_id` | The object's ID |
| `op` | `create`, `version` (a new full state of a single-writer object), `append` (a new record referring to another), `retract` (correction that points at an earlier entry) |
| `payload` | Canonical JSON (RFC 8785) of the contract shape, plus internal provenance (`origin_mode`, author, device) |
| `payload_sha256` | Hash of the canonical payload |
| `causal_refs` | Record IDs this entry depends on |
| `release_id` | The work's pinned configuration release, which can differ from the active release |
| `audience` | Owning module's purpose, object/field projection and policy version; never an implicit export grant |

Records are never updated in place across nodes; a correction is a new entry pointing to the old one; attachments are
content addressed (surface-mobility-comms.md 7.2 item 1).

#### 4.2.1 Atomic append and recovery incarnation

The state mutation, origin-sequence allocation, replication entry, HLC high water, audit, outbox and command ledger
commit in the same hub transaction (SYSTEM 5.7). Rollback allocates no committed sequence. Replication identity is
`(origin_node_id, origin_incarnation, origin_seq)`; every hash covers all entry fields, not just payload. A duplicate
identity with different content is corruption. Record IDs and command deduplication identities do not change on restore.

Restore is an explicit recovery operation, not copying a database over a running writer. Stop and fence the old writer,
revoke its transport credentials, verify the paired backup and record a new incarnation with its backup checkpoint and
parent incarnation. A replacement cannot assert the old custody epoch: restored ownership resources remain fenced
until their transfer heads are reconciled. A peer's old sequence 100 and the restored backup's 80 therefore cannot
collide: new writes use the new incarnation, and missing old entries are fetched or shown as a recovery gap.

Before admitting retries or modifications of restored objects, recover the mission-life deduplication ledger and
resource heads through the current local recovery journal or authorized peer receipt/checkpoint. Every accepted
command has a durable receipt with identity fingerprint and result reference; receipt recovery reveals no private
body. If the checkpoint is incomplete, those commands/objects stay in visible reconciliation mode: never treat absence
from the old backup as proof the command never ran. New captures use new IDs in the new incarnation, and emergency
entry/manual capture remain available. Queued old commands remain pending until their identities are reconciled;
clients must not mint replacement keys for them. No promise of recovering post-backup data from a destroyed disk with
no surviving copy is made. The recovery screen reports the verified cutoff and unresolved accepted-record gaps.
A tested local current recovery medium enables offline recovery without a ground role; a peer is optional.

### 4.3 Exchange protocol

1. **Authorize before materializing.** The owning module evaluates recipient identity, purpose, object consent and
   field grant using SYSTEM 8.3. Recheck at export, before transmission, and at import/read. LAN, files, backups of
   recipient projections and ground transport use this same policy. Unknown grants exclude the object. Journals never
   leave; private reports leave only as an owner-approved snapshot, never as their private-store row.
2. **Recipient streams.** The sender builds a durable stream for `(recipient_id, purpose, policy_epoch)` with a new
   contiguous `delivery_seq` for each authorized projection. An internal scan cursor advances over the local log even
   when all records are excluded; neither excluded IDs, hashes, counts nor skip markers go to the recipient. A wire
   vector is `{stream_id: highest_contiguous_delivery_seq}`, not the raw origin vector. This permits filtering without
   gaps or leaking excluded records. Origin identity for a permitted record is inside its projection; revisions of the
   same allowed object remain idempotent by origin identity plus projection hash.
3. **Send and apply.** Send bounded pages in delivery order. The receiver validates destination, signature, policy
   epoch, schema, source incarnation and projection type. Apply projection, receipt cursor, local audit and authorized
   local event in one transaction. A missing causal reference is either an explicitly redacted/absent optional link or
   a required authorized dependency; required ones wait in a bounded pending buffer, then visible quarantine. Never
   export a forbidden dependency to make a projection complete. Projected objects are typed replica views, never full
   clinical write objects inferred from missing fields.
4. **Duplicates and acknowledgments.** Same stream/sequence and hash is a no-op; different hash stops that stream as
   corruption. ACK advances only the highest contiguous committed delivery sequence. Source log and command ledger
   remain intact. The stable approved snapshot ID and hash identify a ground payload independently of transmission.
5. **Grant changes.** Revocation fences the old policy epoch and cancels unsent projections and cached views; a signed
   control record removes recipient access and receives an acknowledgment. A new grant creates a new stream epoch and
   explicitly authorized backfill; it never sends historical exclusions automatically. Already delivered information
   cannot be recalled, and the owner is told that before sharing. An offline recipient displays its consent snapshot's
   age and cannot forward data beyond its grant. Immediate remote erasure is never claimed.
6. **Transports.** Mutual TLS LAN and signed, recipient-encrypted `.chsync` files carry the same authorized projection
   envelope. HTTP endpoints are pending R-SYS-3; CLI files remain available under the same checks. Ground transport
   carries only explicitly approved HandoffSnapshot/GroundMessage/catch-up projections and consented attachments,
   plus public configuration/receipts. It never carries raw answers, notes, observations, dose records or clinical
   replication log rows merely because a ground vector is behind.

Status shows recipient-visible queue counts, stream cursors and a signed opaque audit checkpoint, never private audit
references. Onboard peers receive only subject-authorized clinical projections needed at that site; P0/P1 operational
facts use their role-specific projections. Local source vectors remain internal for recovery, never an export shortcut.

### 4.4 Conflict rules per record type

| Record type | Writer | Rule when two nodes changed it apart |
|---|---|---|
| Observations, sensor readings, answers, notes, attachments, mission events, reference-only audit receipts | Append only, only authorized projections | Union of distinct record IDs; never a conflict (surface-mobility-comms.md 7.4 item 1) |
| Supersession of an observation | Home node of the observation | A correction is a new record; both stay visible |
| Housekeeping: routine occurrence actions, card dismissals, acknowledgements, reschedules | Any node, per person action | Deterministic last writer wins by HLC; the losing write is kept in history (7.4 item 2). Routine special case (E25 R-E25-17): a person's completion with `completed_at` inside the window beats a timer miss recorded by a node that did not see it |
| Warnings | The node with the sensor or the rule input | Never merged; a replica shows origin node and age. A warning is cleared only by its own node's rule (7.4 item 5) |
| Help sessions, crew broadcasts | Home node (where opened) | Single writer. A peer's actions arrive as proposals and are shown, never applied automatically |
| Episodes, urgency, assessments, reviews, plan runs, guided checks, medication proposals, dose confirmations, administrations, handoffs | Home node | Never auto merged. If two nodes hold diverging versions (for example after a re-homing during a sortie), both are kept and a medical officer chooses ("two versions, choose") |
| Supply ledger | The node holding custody of the stock location | Single writer per custody location; uses recorded elsewhere arrive as proposals; no negative balance can arise from a merge (SYSTEM.md 8.5) |
| Roster, role assignments, skill records, consent, identity corrections | Home node (the base hub for the roster) | Never auto merged; proposals only |
| Configuration releases, evidence releases | Signed bundles | Not merged; each hub validates and activates on its own, with crew confirmation (section 7.6) |
| Journal and private reports | Private store never replicated | Journal stays local; a report explicitly shared by its owner becomes an approved snapshot, not a replicated private-store row |
| Participation (streaks, patches, crew goal) | Derived | Personal derivation on the owner-authorized node only; crew progress uses Q15 opted-in aggregates, never exports individual completions |

### 4.5 What never merges automatically

Clinical content (episodes, urgency and dispositions, assessments, reviews, plan runs, dose decisions,
administrations), roster and authority records, consent, identity corrections, configuration releases and supply
custody transfers. Each conflict produces a visible conflict record listing both versions, their nodes, authors and
HLC for an explicitly authorized onboard reviewer. Officer/acting status alone grants no chart access; acting
clinical review follows Q16 and cannot produce `clinician_assessed` resolution. Custody follows SYSTEM 8.5.1, never
a last-writer merge.

### 4.6 Ground link bundles

- **Shape.** A file whose header mirrors the BPv7 primary block: source and destination endpoint IDs (`ipn:` scheme),
  creation time in DTN time (milliseconds since 2000-01-01T00:00:00Z), per node sequence, lifetime and priority; payload
  CBOR or JSON, compressed, signed with the node key (Ed25519) and encrypted to the destination key
  (surface-mobility-comms.md 7.3 item 1 [SM03]). BPSec (RFC 9172) applies when a real DTN adapter carries it [SM04].
- **Acks.** BPv7 status reports are off by default, so the ground returns application ack bundles listing the record
  IDs and approved payload hashes it holds; the hub keeps every record until acked, resends at later contacts, and shows sent, delivered and read
  separately (7.3 item 3 [SM35]).
- **Priorities.** P0 urgent summaries of a few kilobytes, P1 the daily log of tens of kilobytes, P2 images and raw
  waveforms only on request; never video (7.3 item 4; vehicle-computing.md 3.6).
- **Lifetimes (D11).** Signed environment-profile extension `comms_queue.bundle_lifetime` is an object with positive bounded
  integer `value`, `unit="s"`, `basis`, `source_ids` and `to_be_set_by` provenance, validated at activation. No mission duration
  is a code constant or a guessed default. Missing lifetime disables BPv7 transmission with a visible reason; local
  queues and the existing simulated contact queue continue, without pretending delivery.
- **Expiry and renewal.** Expiry deletes only an envelope, never the approved payload or its queue record. Before the
  next contact a bounded renewal batch rechecks recipient grant and creates a new signed envelope with new creation
  time, sequence and current configured lifetime, retaining approved snapshot ID/hash and P0/P1/P2 priority. Within
  priority use original approval time then ID, so repeated expiry cannot starve an older message. ACK of snapshot
  ID/hash completes all its envelope attempts; a duplicate ACK is harmless. Attempt retry/backoff and inflight caps
  are hub settings (SYSTEM 5.11); exhaustion waits visibly for a later contact or operator retry, never drops the
  message. Revocation cancels unsent content; a renewal cannot enlarge the approved field selection.
- **Contact plan** comes from the environment profile (`comms_queue`, `earth_delay`; GroundLink `source` schedule,
  measured or demo switch; CONTRACTS section 12). Only the ground stage, the Habitat line and the ground silence rule
  read the link (PRD 11, link independence).
- **Uplink.** Configuration releases, evidence releases (E10 request 14.8) and ground replies arrive as signed bundles
  in `<data_dir>/inbox/`; section 7.6.
- **Not claimed**: LunaNet compliance or a DTN implementation; the outbox is bundle shaped (surface-mobility-comms.md
  7.7).

## 5. Security and trust on board

### 5.1 Trust boundary

The hub is the trust boundary. The habitat network is not trusted by default even when it runs WPA3-Enterprise
(vehicle-computing.md 3.3 [VC40]). T0 listens on loopback only. The LAN listener is off until enabled and then requires
TLS 1.3 with the hub's local CA certificate; certificate problems that never appeared on the ground appear in orbit
[VC08], so `crew-health doctor` checks the chain, expiry and clock skew and prints the fix.

### 5.2 Device enrollment

1. The operator runs `crew-health devices enroll --kind tablet|station|laptop|adapter-host|peer-hub` on the hub; it
   prints a one time code and a QR code, valid for `security.enrollment_code_minutes`.
2. The device opens the hub address, installs the hub's local CA profile, and presents the code; the hub records the
   device (ID, kind, name, enrolled by, time) in the audit trail.
3. Peer hubs and adapter hosts receive a client certificate (mutual TLS); browsers receive the CA trust and sign in
   with person credentials as the contract defines.
4. `crew-health devices revoke <device_id>` revokes certificates and ends every session opened from that device.

The contract has no device endpoints and no `device_id` on sessions: request R-SYS-4. Interim: enrollment and
revocation are CLI operations; revocation ends all sessions and the operator rotates the credentials of people who used
the device.

### 5.3 Crew authentication on a shared laptop

- Sessions follow CONTRACTS section 20: `POST /v1/sessions` with kind `crew` (with `person_id`), `station`, `ground`,
  `operator` or `editor`; adapters use per adapter tokens. Roles: `crew`, `crew_medical_officer`, `station`, `ground`,
  `operator`, `editor`, `adapter`; every operation's `x-roles` is enforced (E02).
- Credentials are hashed with Argon2id; attempts are rate limited per person and per device; tokens are bearer tokens
  returned once, short lived, bound to the listener they were issued on.
- The shared laptop rests in a `station` session. A person's session ends at sign-out or after the idle lock time (E02
  policy, hub settings), returning to the station view; nothing private stays on screen.
- `ground` sessions are refused by a flight hub unless the demo profile is active; the ground console runs against the
  ground node.
- Acting medical officer authority is not a role; it is an audited authorization derived from an accepted assignment
  (SYSTEM.md 8.4).

### 5.4 Encryption at rest and in transit

| Data | Protection |
|---|---|
| Whole disk | Operating system full disk encryption (BitLocker, FileVault, LUKS), administered by the ground IT that already manages the laptops (vehicle-computing.md 3.4 [VC05]) |
| `hub.sqlite` and backups | File permissions to the service account; backups encrypted with the node's backup key (AES-256-GCM, `cryptography`) |
| `private.sqlite` (journal, private reports) | AES-256-GCM with a unique random DEK and nonce per entry/version, owner/entry/version bound as associated data. DEK wrapped under a unique entry wrapping key held only in the current local key vault. Owner credentials authorize access; no credential-derived secret can reconstruct an erased wrapping key. Data backups contain ciphertext and wrapped DEKs, never wrapping keys. SYSTEM 7.4 defines revocation, pending deletion and restore tests |
| Sync files and bundles | Signed (Ed25519 node key) and encrypted to the destination (X25519 and AES-256-GCM) |
| In transit | Loopback HTTP for T0; TLS 1.3 on the LAN listener; mutual TLS between hubs; bundles protected end to end |

In mission medical systems must include record encryption and privacy for care [EV02] (vehicle-computing.md 3.8).
NASA-STD-3001 Volume 1 requires electronic medical records with encryption, secured per the Privacy Act, and private
medical communications (medical-systems-standards.md 1.2 and 3.4 [EV02]).

### 5.5 What a lost tablet exposes

| On the device | Exposure | Mitigation |
|---|---|---|
| Configuration cache (help flows, plans, copy) | Mission content, no personal data | None needed |
| Queued commands of the last signed-in person | Answers and help actions not yet accepted | Encrypted in IndexedDB with a non-extractable WebCrypto key; partitioned by person; replayed only under that person's session; quarantined under the original owner grant if revoked; a medical officer receives no automatic access; never silently discarded |
| Rendered views | Only while a session is open and unlocked | Idle lock; tokens expire; revocation ends sessions |
| Journal, private reports | Never cached on a client | n/a |

### 5.6 Audit

Every command writes an audit entry in its transaction into a hash chain (SYSTEM.md 5.9). Session creation and end,
enrollment, revocation, sync import, bundle in and out, release activation and rollback, restore and every break glass
read are audited. Audit entries hold references and state changes, never journal text, symptoms or prompts (CONTRACTS
"Audit logs store references"). Only the signed chain head checkpoint travels in authorized sync status and ground bundles, so a peer or the ground
can detect a rewritten history.

## 6. Settings and files on a node

### 6.1 Data directory

`<data_dir>` defaults per operating system (`%PROGRAMDATA%\CrewHealth` on Windows, `/Library/Application Support/CrewHealth`
on macOS, `/var/lib/crew-health` on Linux; a user directory in development) and is set with `CREW_HEALTH_DATA_DIR`.

```
<data_dir>/
  hub.sqlite                 records, events, audit, outbox, idempotency, command ledger, replication log
  private.sqlite             journal and private reports, encrypted per entry
  key-vault/                 live entry wrapping keys and monotonic revocations; excluded from snapshots
  knowledge/                 evidence releases (E10), content addressed
  research/                  research catalog (E10) and workbench (E19) stores, ground and editor hosts only
  config/releases/           validated configuration releases by ID (active and inactive slots)
  outbox/ inbox/             ground bundles
  sync/                      sync files in and out
  backups/                   rolling encrypted snapshots
  crash/                     crash reports kept for the next downlink
  logs/                      structured logs (no private content)
```

### 6.2 Deployment settings

`<data_dir>/crew-health.toml` holds deployment settings that are not mission content: `node.id`, `profile`
(`flight`, `ground`, `demo`), listeners and ports, `network.lan.enabled`, TLS paths, which workers run, and the hub
settings document path. Operational bounds (queue sizes, timeouts, retries, cache sizes) live in the hub settings
document of SYSTEM.md 5.11, each with its basis. Mission content (thresholds, cadences, words, steps) lives only in signed configuration documents: the
18 base documents plus accepted decision-overlay extensions such as D12 unit mappings (CONTRACTS section 11).

## 7. Packaging, install, update and recovery

### 7.1 Native single command

`uv run crew-health` starts the supervisor with the T0 defaults: the hub on `127.0.0.1:8080` serving the built crew app,
and the camera worker when a camera is present. No containers. Subcommands (E20 owns the CLI shell; each epic owns its
own subcommand):

| Command | Does |
|---|---|
| `crew-health` or `crew-health run` | Supervisor plus the enabled processes |
| `crew-health doctor` | Checks Python, disk space, database integrity, TLS chain, clock skew, camera, BLE, ports, active release |
| `crew-health backup` / `restore <file>` / `restore --golden` | Online backup; restore on this or another machine; golden restore |
| `crew-health verify` | `PRAGMA integrity_check`, audit chain verification, release hashes |
| `crew-health release install <bundle>` / `activate <id>` / `rollback` | Configuration releases (E01) |
| `crew-health-knowledge install <bundle>` | Evidence releases (E10) |
| `crew-health devices enroll` / `revoke` | Section 5.2 |
| `crew-health sync export` / `import` | Section 4.3 file transport |
| `crew-health help print-cards` | Printed help cards from the active release (E26) |
| `crew-health install <bundle>` / `rollback-software` | Offline software install and rollback (7.4) |

### 7.2 Supervisor and watchdog

The supervisor is a small Python process with no domain code. It starts each enabled process, polls its health check,
restarts it with exponential backoff and records crash reports in `<data_dir>/crash/` for the next downlink (the EXPRESS
fix, vehicle-computing.md 2.4 [VC15]). Policies per process are in SYSTEM.md section 3. The hub is never given up on:
after its burst limit it keeps restarting at the longest backoff and the station display shows the failure and the
printed card fallback. For flight installs the supervisor itself runs as an operating system service (systemd with
`WatchdogSec`, launchd `KeepAlive`, or a Windows service), so a supervisor crash is also restarted. On start the hub
runs `PRAGMA quick_check`, verifies the audit chain tail and the active release hashes before accepting commands; a
failure starts read-only safe mode and offers restore.

### 7.3 Containers (OCI compose profiles)

T0 is native and needs no containers. Linux images run non-root with read-only roots and bounded writable data/tmp
volumes. The signed image manifest lists hub, camera (Linux with an explicitly passed video device), adapters, model,
embedding, forecast, groundlink and ground console. LIVE, REPLAY and SIM adapters require an explicit profile;
`crew` never starts demonstration inputs automatically.

| Profile | Services | Connectivity gate |
|---|---|---|
| `minimal` | hub | Browser to loopback published hub port and static emergency cache |
| `crew` | hub, selected adapters, camera when enabled | Authenticated ingest and worker health through private service DNS |
| `full` | crew plus model, embedding, forecast, groundlink | One bounded request/response per enabled worker and an isolated-worker failure test |
| `ground` | ground node, groundlink, console | Authorized bundle import and console read; no onboard dependency |

Native processes bind `127.0.0.1` (and explicit `::1` when used), with per-service random tokens loaded through
service-account protected files. Health can be unauthenticated only on that local private listener. A remote camera
or adapter host uses enrolled mutual TLS on the hub LAN listener; no frames cross it.

Linux compose creates an internal bridge. Workers bind `0.0.0.0:<port>` **inside that isolated container network**,
with no host `ports` mapping, no host networking and per-service tokens or mutual TLS. Hub addresses workers by
service DNS (`camera`, `model`, `forecast`, etc.), never another container's loopback. Only the hub is published at
host `127.0.0.1:8080`; optional LAN `8443` needs explicit configuration, TLS and enrollment. Network policy permits
only required hub/worker edges. The image/profile smoke test verifies service discovery, authenticated calls and that
worker ports cannot be reached from the host LAN.

Apple Silicon `minimal` and `crew` may use containers for hub/adapters, but Metal inference and the camera run native.
The native worker still binds loopback. A supervised native gateway exposes a Unix-domain socket in a private shared
runtime directory; a companion container mounts only that socket and bridges it onto the internal compose network
with a service token. The gateway connects to native loopback, bounds calls and authenticates the container client.
Docker Desktop/Podman profile packaging must prove socket sharing and request/response connectivity; if that runtime
cannot pass, `full` runs wholly native on macOS. It never opens a native Metal worker on a host external interface or
assumes `host.docker.internal` can reach a loopback-only listener. The wholly native macOS profile is required and
fully usable without Docker. Linux native needs no bridge; Windows uses native loopback for the T0 profile.

Resource budgets are SYSTEM 3 settings: hub 768 MB and 2 CPUs; camera 1 GB and 2 CPUs; adapters 256 MB; model 7.5 GB
and 6 CPUs; embedding 768 MB; forecast 1.5 GB and 2 CPUs; groundlink 256 MB. Model and forecast are never resident
together on the 16 GB reference machine. Measure total RSS including browser/OS at SYSTEM 8.7; unload embedding after
retrieval if needed, and do not claim an unmeasured budget fits. No optional worker exhaustion can stop help or capture.

### 7.4 Offline install and update

No network on board (vehicle-computing.md 3.4). Every software update is one signed artifact, as MEC disk images,
Playbook's self-contained VM and HDTN builds uploaded to a laptop were [VC18] [VC23] [VC10]:

- `crew-health-<version>-<os>-<arch>.chpkg` contains a native signed installer/bootstrap executable, a pinned
  relocatable Python 3.14 interpreter with standard library, `uv`, the complete platform wheelhouse, locked dependency
  manifest, built crew/ground assets, migrations, CA material, SBOM/licences and signed hashes. No system Python,
  compiler, Node, npm, pnpm or online package resolution is required. Built UI uses a preinstalled supported browser,
  checked by bootstrap; offline browser installer is included where redistribution permits, otherwise the signed
  prerequisites manifest identifies the verified installed browser version before deployment.
- Per supported target (Windows x86_64, Linux x86_64, macOS arm64; other architectures only when their CI artifact
  passes), bundle all redistributable native dependencies: OpenCV/video libraries, SQLite extensions, BLE dependencies,
  and runtime libraries. Linux declares exact minimum kernel/glibc and requires BlueZ/video permissions only for the
  optional adapters. Windows includes the required redistributable runtime installer. macOS declares minimum OS/Metal
  framework version, architecture and code-signing requirements. `doctor` verifies these without network. Absent
  optional hardware never fails hub/manual-entry installation.
- Native `llama-server` and embedding executables plus their matching CPU/Metal shared libraries are included for each
  enabled target; Apple Metal shaders/resources are bundled, requiring no compiler download. Forecast artifacts contain
  the pinned PyTorch runtime and all libraries. Weights and evidence are separate signed offline artifacts with hashes;
  absence reports model unavailable while all onboard safety flows work. OCI distribution includes exported images and
  its verified offline container-runtime prerequisite or installer; native T0 remains independent of it.
- Bootstrap verifies the artifact using a pinned mission software key, installs to a new version directory and uses
  the bundled interpreter with `uv sync --offline --find-links <wheelhouse>` and the bundled lock. It checks no URL
  dependencies remain, runs doctor, takes a paired backup, applies additive migrations and switches `current` atomically.
  Previous software stays installed. In-mission migrations expand only; contraction requires a separate migration plan
  and cannot be smuggled into rollback.
- Software rollback switches code to the previous compatible version without rolling data or deduplication back.
  Data restore is the fenced operation below. CI tests clean install, upgrade and rollback with network disabled and
  empty package/runtime caches on every target, including native Metal startup on an Apple runner. Any external
  prerequisite is verified as part of the image manifest, never silently assumed from a developer's laptop.

### 7.5 Golden image and restore

A read-only golden copy contains the verified software, last good signed configuration and a verified paired backup
manifest on separate media. It contains no key-vault secrets. `crew-health restore --golden` verifies both stores and
the audit head, applies current vault revocations, reconciles the current command receipt checkpoint and performs the
new-incarnation fencing in 4.2.1 before mutations resume. Restoring the same backup twice creates two different
incarnations; never resume old counters or custody. A lost/replaced machine needs local operator fencing of the former
writer and current recovery material, not ground permission. Unknown current heads remain visible reconciliation work;
printed help, static emergency entry and new local records remain available.

Backup and restore share SYSTEM 5.8.1's barrier, manifest, reference checks and crash matrix; `integrity_check` alone
cannot prove journal consistency or recover keys. A separate current vault recovery device, when used, participates
in deletion acknowledgments and cannot be a historical backup. Test restoring a pre-deletion backup with retained
and revoked entries, and a sequence-80 backup after a peer has sequence 100, including duplicate command retries.

### 7.6 Configuration and evidence updates through the ground link

Configuration works like flight tables (AGENTS.md section 3; the cFS table pattern): a release arrives as a signed
bundle in the inbox; the loader validates it into the inactive slot with all 16 checks and registered semantic checks/guards.
An authenticated editor or operator activates it with the current release ETag (D8), with the change visible
(surface-mobility-comms.md 7.4 item 4 [V2 10126]). Activation is atomic; the previous release stays for rollback;
open help sessions and plan runs keep their pinned versions.
They do not block activation. Medical-officer status alone does not authorize activation. D1 keeps active warnings
on their original definition until their pinned clearing rule or an authorized resolution with reason; activation
cannot lower them. Q1/Q7/Q18 do not make absent clinical sign-off a runtime activation gate. Audit retains the transport
provenance until R-SYS-14 adds it to the release view: bundle hash, signer key ID, source release and the ground message that carried it (request R-SYS-14 adds
these to ConfigRelease). Evidence releases (E10) follow the same path.

## 8. CI and container plan

GitHub Actions, one workflow per concern; every gate produces its evidence as an artifact.

| Job | Runs | Gates |
|---|---|---|
| `contracts` | Base contract SHA guard plus D12 decision-overlay traceability and effective-schema checks; regenerate Pydantic and TypeScript types and diff against committed; validate every configuration document and `knowledge/**` against schemas; no en or em dash in owned files | Any drift or invalid document |
| `lint` | `ruff check`, `ruff format --check`, `mypy --strict` per package, `lint-imports`, `eslint`, `prettier --check`, `vue-tsc` | Any warning (AGENTS.md code rule 10) |
| `hub-tests` | `pytest` with branch coverage on Ubuntu, macOS and Windows runners (the ISS and Orion fleets are Windows [VC03] [VC25]) | Failures; branch coverage below 95% on `domain` and `application` |
| `property` | Hypothesis and fast-check suites for parsers, rule engines and unit conversions | Failures |
| `faults` | Fault injection: kill during write, disk full, clock jump, corrupt configuration, model stopped, devices absent, link down | Any accepted record lost; any safety function unavailable |
| `ui-tests` | `pnpm -C ui/crew typecheck && test`, the same for `ui/ground` | Failures |
| `contract-tests` | Schemathesis against the running hub with the effective OpenAPI from the guarded base and D12 overlay; UI against recorded examples | Any response that breaks the contract |
| `e2e` | The demo fixture `fixtures/demo-mara-fd19` through its six acts in the `minimal` compose profile with Playwright, model stopped where the scenario allows; the help first screen latency check | Failing V or D scenario; help first screen over 500 ms |
| `traceability` | The R, V and D matrix from pytest markers | An untested requirement in a built epic |
| `mutation` | `mutmut` on the critical path packages (nightly and on demand) | Score below the epic's threshold |
| `supply-chain` | CycloneDX SBOM for Python (`cyclonedx-py`) and npm (`@cyclonedx/cyclonedx-npm`); `pip-audit` on the exported lock; `pnpm audit`; licence report into `THIRD-PARTY.md` | Known vulnerable dependency without a recorded verdict; unknown licence |
| `images` | Build every image with BuildKit, attach the SBOM, scan, smoke test each compose profile's health checks | Unhealthy service |
| `bundle` | Build each OS/architecture artifact; verify install, upgrade, rollback and required runtime launch with network disabled and empty caches | Any step fails |

The reference deployment measurements (SYSTEM.md 8.7) need the real laptop and camera and are recorded by hand into
`eval/` with the machine described; CI checks that the record exists and is not older than the build it describes.

## 9. Targets and fallbacks mapped to tiers and mechanisms

PRD section 11 targets:

| Target | Tier | Mechanism |
|---|---|---|
| Someone needs help first screen in 500 ms, model stopped, under load | T0 | Help guidance depends on E01, E02, E23 and the early E27-guidance rule; first screen rendered from the client cache before any network call; hub serves it without touching the model; e2e latency check under combined load (SYSTEM.md 8.7) |
| Warnings never raised or lowered by the assistant | T0 | Domain types and transitions in E24, SQLite constraints, API roles (E24 section 9) |
| Sensors: no stale or offline reading shown as current; Replay shows its original date | T4 | Freshness states from `observed_at`; origin and age on every value; E23 freshness sweep before ingest resumes |
| Routine: no misses filled in | T0 | Deterministic occurrences; timers only on the person's current hub (4.4) |
| Configuration: nothing held in client code | T0 | Codegen from contracts; client renders served text; CI `contracts` job |
| Data durability: no lost accepted record, idempotent commands | T0 | WAL with `synchronous=FULL`; one command one transaction; 24 hour idempotency cache plus the durable command ledger (SYSTEM.md 8.6); `faults` job |
| Privacy: zero cross-patient, journal or report leakage | T0 to T7 | Object-level authorization, event audience filter, cache classes, ground projection (SYSTEM.md 8.3); entry keys and current vault revocations (5.4) |
| Tool integrity | T0 | Typed tool gateway (E12); domain checks before anything is recorded |
| Usability: origin and states distinguishable | T0 | Origin word and age on every value; designed states from ui-copy |
| Camera pulse MAE ≤ 5 bpm, bias, rejection | T0 webcam, T3 oximeter reference | Camera worker quality gate refuses rather than guesses; venue protocol (E06) |
| Kit map counts computed, none typed | T0 | Server computed views from kit-map and attached devices (E27) |
| Plans aboard counts equal everywhere | T0 | One server computation (E28) |
| Link independence | T0, T7 | Only three readers of GroundLink (E29); `faults` job runs plans, help, warnings and routine with the link up, delayed and down |
| Crewmate counts | T0, T3 | Teach and check (E30) with the camera reference when present |
| CPR rate (experimental) | T0 webcam or T2 treatment camera | E30 with the camera worker; labeled Experimental |
| Roles and drills | T0 | E31 rules; subject and walkers excluded |
| Supplies: no use below zero, no typed cover | T0, T6 | Single writer per custody location (4.4) |
| Offline cache: help and active plan render with the hub stopped | T1, T2 | Client cache by version (SYSTEM.md 8.3, E26, E28) |
| Assistant turn time on the M3 16 GB | T0 with model | Model server budget and measurement (SYSTEM.md 8.7) |
| Rules against the assistant | T0 | E18 and E21 run both on frozen scenarios |
| Camera breathing MAE ≤ 2 | T0 | Camera worker (E06) |
| Band | T3 | E08 adapter, sync and scoring |
| Forecast | T0 with forecast worker | Isolated worker; deterministic baselines when absent (E14) |
| LLM | T0 with model | E18 harness |
| Evidence | T0 | Shared eligibility rule (SYSTEM.md 8.2) and sourced purpose-eligible passages with Q1/Q18 review labels; fixed "not supported" state |
| Recovery: network, model, device loss, clock skew, duplicates, corrupt files visible | T0 to T7 | Supervisor, health states, idempotent ingest, clock correction events, release last good, quarantine (4.3) |

Charter fallback ladder:

| Missing part | Tier where it appears | What keeps working, and how |
|---|---|---|
| Language model | T0 | Every guided flow from configured question sets; assistant panel says it is off; model server not started or crashed is a degraded component only |
| Camera or poor signal | T0 | Manual pulse and breathing entry; "Not measured" with the reason from the quality gate |
| Oximeter, cuff, thermometer, band | T3 | Manual entry; kit map counts change live from attached devices |
| Habitat sensor feed | T4, T5 | Manual readings, REPLAY or SIM, each labeled; stale never shown as current |
| Ground link | T7 | Everything; queue size and last contact shown |
| Crew tablets | T2 | The laptop is a complete station (T0) |
| Forecasting model | T0 | Deterministic baselines; the second view absent and labeled |
