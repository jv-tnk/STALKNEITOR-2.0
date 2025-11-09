import { relations } from "drizzle-orm";
import {
  boolean,
  index,
  integer,
  jsonb,
  pgEnum,
  pgTable,
  primaryKey,
  text,
  timestamp,
  uniqueIndex,
  uuid,
} from "drizzle-orm/pg-core";

export const progressStatusEnum = pgEnum("progress_status", [
  "not_started",
  "in_progress",
  "skipped",
  "done",
]);

export const eventKindEnum = pgEnum("event_kind", [
  "problem_done",
  "module_done",
  "streak_extended",
  "team_joined",
  "note_shared",
]);

export const users = pgTable(
  "users",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    email: text("email").notNull(),
    name: text("name"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    emailIdx: uniqueIndex("users_email_idx").on(table.email),
  }),
);

export const teams = pgTable(
  "teams",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    name: text("name").notNull(),
    ownerId: uuid("owner_id").references(() => users.id, {
      onDelete: "set null",
    }),
    isPublic: boolean("is_public").notNull().default(false),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    ownerIdx: index("teams_owner_idx").on(table.ownerId),
  }),
);

export const teamMembers = pgTable(
  "team_members",
  {
    teamId: uuid("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    role: text("role").notNull().default("member"),
    joinedAt: timestamp("joined_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.teamId, table.userId] }),
    memberIdx: index("team_members_user_idx").on(table.userId),
  }),
);

export const teamInvites = pgTable(
  "team_invites",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    teamId: uuid("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    token: text("token").notNull(),
    role: text("role").notNull().default("member"),
    expiresAt: timestamp("expires_at", { withTimezone: true }),
    createdBy: uuid("created_by").references(() => users.id, {
      onDelete: "set null",
    }),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    invitesTokenIdx: uniqueIndex("team_invites_token_idx").on(table.token),
  }),
);

export const modules = pgTable(
  "modules",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    guideModuleId: text("guide_module_id").notNull(),
    title: text("title").notNull(),
    division: text("division").notNull(),
    orderIndex: integer("order_index").notNull(),
    url: text("url").notNull(),
    guideVersion: text("guide_version"),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    uniqueGuideModule: uniqueIndex("modules_guide_module_id_idx").on(
      table.guideModuleId,
    ),
    divisionIdx: index("modules_division_order_idx").on(
      table.division,
      table.orderIndex,
    ),
  }),
);

export const problems = pgTable(
  "problems",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    uniqueId: text("unique_id").notNull(),
    name: text("name").notNull(),
    url: text("url").notNull(),
    source: text("source"),
    difficulty: text("difficulty"),
    tags: text("tags").array(),
    guideModuleId: text("guide_module_id")
      .notNull()
      .references(() => modules.guideModuleId),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    problemsUniqueIdx: uniqueIndex("problems_unique_id_idx").on(table.uniqueId),
    problemsModuleIdx: index("problems_module_idx").on(
      table.guideModuleId,
    ),
  }),
);

export const moduleProgress = pgTable(
  "module_progress",
  {
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    guideModuleId: text("guide_module_id")
      .notNull()
      .references(() => modules.guideModuleId, { onDelete: "cascade" }),
    status: progressStatusEnum("status").notNull(),
    percent: integer("percent").notNull().default(0),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.userId, table.guideModuleId] }),
  }),
);

export const problemProgress = pgTable(
  "problem_progress",
  {
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    problemId: uuid("problem_id")
      .notNull()
      .references(() => problems.id, { onDelete: "cascade" }),
    status: progressStatusEnum("status").notNull(),
    attempts: integer("attempts").notNull().default(0),
    lastResult: text("last_result"),
    updatedAt: timestamp("updated_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    pk: primaryKey({ columns: [table.userId, table.problemId] }),
  }),
);

export const notes = pgTable("notes", {
  id: uuid("id").primaryKey().defaultRandom(),
  userId: uuid("user_id")
    .notNull()
    .references(() => users.id, { onDelete: "cascade" }),
  guideModuleId: text("guide_module_id")
    .notNull()
    .references(() => modules.guideModuleId, { onDelete: "cascade" }),
  problemId: uuid("problem_id").references(() => problems.id, {
    onDelete: "set null",
  }),
  content: text("content").notNull(),
  visibility: text("visibility").notNull().default("private"),
  createdAt: timestamp("created_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  updatedAt: timestamp("updated_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
});

export const events = pgTable(
  "events",
  {
    id: uuid("id").primaryKey().defaultRandom(),
    teamId: uuid("team_id")
      .notNull()
      .references(() => teams.id, { onDelete: "cascade" }),
    userId: uuid("user_id")
      .notNull()
      .references(() => users.id, { onDelete: "cascade" }),
    kind: eventKindEnum("kind").notNull(),
    payload: jsonb("payload_json").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true })
      .notNull()
      .defaultNow(),
  },
  (table) => ({
    eventsTeamIdx: index("events_team_created_at_idx").on(
      table.teamId,
      table.createdAt,
    ),
  }),
);

export const etlRuns = pgTable("etl_runs", {
  id: uuid("id").primaryKey().defaultRandom(),
  commitSha: text("commit_sha").notNull(),
  startedAt: timestamp("started_at", { withTimezone: true })
    .notNull()
    .defaultNow(),
  finishedAt: timestamp("finished_at", { withTimezone: true }),
  modulesUpserted: integer("modules_upserted").notNull().default(0),
  problemsUpserted: integer("problems_upserted").notNull().default(0),
  errors: jsonb("errors"),
});

export const usersRelations = relations(users, ({ many }) => ({
  memberships: many(teamMembers),
  notes: many(notes),
}));

export const teamsRelations = relations(teams, ({ many, one }) => ({
  owner: one(users, {
    fields: [teams.ownerId],
    references: [users.id],
  }),
  members: many(teamMembers),
  events: many(events),
}));

export const modulesRelations = relations(modules, ({ many }) => ({
  problems: many(problems),
  notes: many(notes),
}));

export const problemsRelations = relations(problems, ({ one }) => ({
  module: one(modules, {
    fields: [problems.guideModuleId],
    references: [modules.guideModuleId],
  }),
}));

export const notesRelations = relations(notes, ({ one }) => ({
  module: one(modules, {
    fields: [notes.guideModuleId],
    references: [modules.guideModuleId],
  }),
  problem: one(problems, {
    fields: [notes.problemId],
    references: [problems.id],
  }),
}));

export const teamInvitesRelations = relations(teamInvites, ({ one }) => ({
  team: one(teams, {
    fields: [teamInvites.teamId],
    references: [teams.id],
  }),
  creator: one(users, {
    fields: [teamInvites.createdBy],
    references: [users.id],
  }),
}));
