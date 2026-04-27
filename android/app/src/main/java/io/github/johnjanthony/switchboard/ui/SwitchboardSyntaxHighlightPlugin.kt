package io.github.johnjanthony.switchboard.ui

import io.noties.markwon.AbstractMarkwonPlugin
import io.noties.markwon.MarkwonConfiguration

class SwitchboardSyntaxHighlightPlugin : AbstractMarkwonPlugin() {
	private val highlight = SwitchboardSyntaxHighlight()

	override fun configureConfiguration(builder: MarkwonConfiguration.Builder) {
		builder.syntaxHighlight(highlight)
	}
}
