import { test } from 'node:test';
import assert from 'node:assert/strict';
import { answerCmd, awayOnCmd, awayOffCmd, spawnFreshCmd, resumeCmd, combineCmd, forceEndCmd, setHiddenCmd } from './commands.js';

const FIXED_ISO = '2026-06-15T12:00:00.000Z';
const nowIso = () => FIXED_ISO;

test('answerCmd builds the answer write at conversations/<id>/answers/<request_id>', () => {
	const cmd = answerCmd('c1', 'r9', 'hello', 'John', nowIso);
	assert.deepEqual(cmd, {
		path: 'conversations/c1/answers/r9',
		value: { text: 'hello', sender: 'John', request_id: 'r9', written_at: FIXED_ISO },
	});
});

test('awayOnCmd pushes enter_global into away_mode_commands', () => {
	const cmd = awayOnCmd(nowIso);
	assert.equal(cmd.path, 'away_mode_commands');
	assert.deepEqual(cmd.value, { type: 'enter_global', issued_at: FIXED_ISO });
});

test('awayOffCmd({}) yields exit_global with NO decision and NO default_text keys', () => {
	const cmd = awayOffCmd({}, nowIso);
	assert.equal(cmd.path, 'away_mode_commands');
	assert.deepEqual(cmd.value, { type: 'exit_global', issued_at: FIXED_ISO });
	assert.equal('decision' in cmd.value, false);
	assert.equal('default_text' in cmd.value, false);
});

test('awayOffCmd with send_default decision includes decision and default_text', () => {
	const cmd = awayOffCmd({ decision: 'send_default', defaultText: 'busy now' }, nowIso);
	assert.deepEqual(cmd.value, {
		type: 'exit_global',
		issued_at: FIXED_ISO,
		decision: 'send_default',
		default_text: 'busy now',
	});
});

test('awayOffCmd with skip decision includes decision but not default_text', () => {
	const cmd = awayOffCmd({ decision: 'skip' }, nowIso);
	assert.deepEqual(cmd.value, { type: 'exit_global', issued_at: FIXED_ISO, decision: 'skip' });
	assert.equal('default_text' in cmd.value, false);
});

test('spawnFreshCmd builds a fresh spawn with surface and project', () => {
	const cmd = spawnFreshCmd({ surface: 'windows', project: 'C:/Work/X' }, nowIso);
	assert.equal(cmd.path, 'spawn_commands');
	assert.deepEqual(cmd.value, { type: 'fresh', surface: 'windows', project: 'C:/Work/X', issued_at: FIXED_ISO });
});

test('spawnFreshCmd includes optional prompt and target_conversation_id when given', () => {
	const cmd = spawnFreshCmd({ surface: 'wsl', project: '/work/y', prompt: 'go', targetConversationId: 'c2' }, nowIso);
	assert.deepEqual(cmd.value, {
		type: 'fresh',
		surface: 'wsl',
		project: '/work/y',
		issued_at: FIXED_ISO,
		prompt: 'go',
		target_conversation_id: 'c2',
	});
});

test('resumeCmd has source_conversation_id and NO surface/project/target', () => {
	const cmd = resumeCmd({ sourceConversationId: 'c3' }, nowIso);
	assert.equal(cmd.path, 'spawn_commands');
	assert.deepEqual(cmd.value, { type: 'resume', source_conversation_id: 'c3', issued_at: FIXED_ISO });
	assert.equal('surface' in cmd.value, false);
	assert.equal('project' in cmd.value, false);
	assert.equal('target_conversation_id' in cmd.value, false);
});

test('resumeCmd includes optional prompt when given', () => {
	const cmd = resumeCmd({ sourceConversationId: 'c3', prompt: 'continue' }, nowIso);
	assert.deepEqual(cmd.value, { type: 'resume', source_conversation_id: 'c3', issued_at: FIXED_ISO, prompt: 'continue' });
});

test('combineCmd builds source/target combine command', () => {
	const cmd = combineCmd({ sourceConversationId: 'c1', targetConversationId: 'c2' }, nowIso);
	assert.equal(cmd.path, 'combine_commands');
	assert.deepEqual(cmd.value, { source_conversation_id: 'c1', target_conversation_id: 'c2', issued_at: FIXED_ISO });
});

test('forceEndCmd builds the force-end command', () => {
	const cmd = forceEndCmd({ conversationId: 'c7' }, nowIso);
	assert.equal(cmd.path, 'force_end_commands');
	assert.deepEqual(cmd.value, { conversation_id: 'c7', issued_at: FIXED_ISO });
});

test('setHiddenCmd targets meta/hidden with the boolean', () => {
	assert.deepEqual(setHiddenCmd('c1', true), { path: 'conversations/c1/meta/hidden', value: true });
	assert.deepEqual(setHiddenCmd('c1', false), { path: 'conversations/c1/meta/hidden', value: false });
});
