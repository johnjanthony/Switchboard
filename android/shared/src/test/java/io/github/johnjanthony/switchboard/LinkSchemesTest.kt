package io.github.johnjanthony.switchboard

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class LinkSchemesTest {
	@Test
	fun `http https and mailto pass, case-insensitively`() {
		assertTrue(isAllowedLinkScheme("http://example.com"))
		assertTrue(isAllowedLinkScheme("https://example.com/path?q=1"))
		assertTrue(isAllowedLinkScheme("HTTPS://EXAMPLE.COM"))
		assertTrue(isAllowedLinkScheme("mailto:john@example.com"))
	}

	@Test
	fun `dialer sms store and script schemes are blocked`() {
		assertFalse(isAllowedLinkScheme("tel:+15551234567"))
		assertFalse(isAllowedLinkScheme("sms:+15551234567"))
		assertFalse(isAllowedLinkScheme("market://details?id=x"))
		assertFalse(isAllowedLinkScheme("intent://scan/#Intent;end"))
		assertFalse(isAllowedLinkScheme("javascript:alert(1)"))
		assertFalse(isAllowedLinkScheme("file:///etc/hosts"))
		assertFalse(isAllowedLinkScheme("data:text/html,x"))
	}

	@Test
	fun `scheme-less and fragment links are blocked`() {
		assertFalse(isAllowedLinkScheme("relative/path"))
		assertFalse(isAllowedLinkScheme("#anchor"))
		assertFalse(isAllowedLinkScheme(""))
	}
}
