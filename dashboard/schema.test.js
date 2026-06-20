import { test } from 'node:test';
import assert from 'node:assert/strict';
import * as schema from './schema.js';

test('conversations() is the conversations root', () => {
	assert.equal(schema.conversations(), 'conversations');
});

test('conversationMeta(id) targets the meta child', () => {
	assert.equal(schema.conversationMeta('c1'), 'conversations/c1/meta');
});

test('membersActive(id) targets members_active', () => {
	assert.equal(schema.membersActive('c1'), 'conversations/c1/members_active');
});

test('pendingQuestions(id) targets pending_questions', () => {
	assert.equal(schema.pendingQuestions('c1'), 'conversations/c1/pending_questions');
});

test('agentStatus(id) targets agent_status', () => {
	assert.equal(schema.agentStatus('c1'), 'conversations/c1/agent_status');
});

test('messages(id) targets messages', () => {
	assert.equal(schema.messages('c1'), 'conversations/c1/messages');
});

test('answer(id, requestId) targets answers/<request_id>', () => {
	assert.equal(schema.answer('c1', 'r9'), 'conversations/c1/answers/r9');
});

test('metaHidden(id) targets meta/hidden', () => {
	assert.equal(schema.metaHidden('c1'), 'conversations/c1/meta/hidden');
});

test('globalAway() targets global_settings/away_mode', () => {
	assert.equal(schema.globalAway(), 'global_settings/away_mode');
});

test('openConversationId() targets global_settings/open_conversation_id', () => {
	assert.equal(schema.openConversationId(), 'global_settings/open_conversation_id');
});

test('wslAvailable() targets global_settings/wsl_available', () => {
	assert.equal(schema.wslAvailable(), 'global_settings/wsl_available');
});

test('adminNotifications() is the admin_notifications root', () => {
	assert.equal(schema.adminNotifications(), 'admin_notifications');
});

test('awayCommands() is the away_mode_commands root', () => {
	assert.equal(schema.awayCommands(), 'away_mode_commands');
});

test('spawnCommands() is the spawn_commands root', () => {
	assert.equal(schema.spawnCommands(), 'spawn_commands');
});

test('combineCommands() is the combine_commands root', () => {
	assert.equal(schema.combineCommands(), 'combine_commands');
});

test('forceEndCommands() is the force_end_commands root', () => {
	assert.equal(schema.forceEndCommands(), 'force_end_commands');
});
