package io.github.johnjanthony.switchboard.ui

import android.graphics.Typeface
import android.text.SpannableString
import android.text.Spanned
import android.text.style.ForegroundColorSpan
import android.text.style.StyleSpan
import dev.snipme.highlights.Highlights
import dev.snipme.highlights.model.BoldHighlight
import dev.snipme.highlights.model.ColorHighlight
import dev.snipme.highlights.model.SyntaxLanguage
import dev.snipme.highlights.model.SyntaxThemes
import io.noties.markwon.syntax.SyntaxHighlight

class SwitchboardSyntaxHighlight : SyntaxHighlight {
	override fun highlight(info: String?, code: String): CharSequence {
		val span = SpannableString(code)
		// Only highlight fences with a recognized language. Plain/unknown fences (prose, commit
		// messages) render as plain monospace, not mis-colored generic highlighting.
		val language = languageFor(info) ?: return span
		val results = try {
			Highlights.Builder()
				.code(code)
				.language(language)
				.theme(THEME)
				.build()
				.getHighlights()
		} catch (_: Throwable) {
			return span
		}
		val len = code.length
		for (h in results) {
			val start = h.location.start.coerceIn(0, len)
			val end = h.location.end.coerceIn(start, len)
			if (start == end) continue
			when (h) {
				is ColorHighlight -> span.setSpan(
					ForegroundColorSpan(h.rgb or 0xFF000000.toInt()),
					start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
				)
				is BoldHighlight -> span.setSpan(
					StyleSpan(Typeface.BOLD),
					start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE,
				)
			}
		}
		return span
	}

	private fun languageFor(info: String?): SyntaxLanguage? {
		val tag = info?.trim()?.lowercase()?.takeIf { it.isNotEmpty() } ?: return null
		return ALIASES[tag]
	}

	companion object {
		private val THEME = SyntaxThemes.darcula(darkMode = true)

		private val ALIASES: Map<String, SyntaxLanguage> = mapOf(
			"bash" to SyntaxLanguage.SHELL,
			"sh" to SyntaxLanguage.SHELL,
			"shell" to SyntaxLanguage.SHELL,
			"zsh" to SyntaxLanguage.SHELL,
			"console" to SyntaxLanguage.SHELL,
			"c" to SyntaxLanguage.C,
			"h" to SyntaxLanguage.C,
			"cpp" to SyntaxLanguage.CPP,
			"c++" to SyntaxLanguage.CPP,
			"cc" to SyntaxLanguage.CPP,
			"cxx" to SyntaxLanguage.CPP,
			"hpp" to SyntaxLanguage.CPP,
			"coffee" to SyntaxLanguage.COFFEESCRIPT,
			"coffeescript" to SyntaxLanguage.COFFEESCRIPT,
			"cs" to SyntaxLanguage.CSHARP,
			"csharp" to SyntaxLanguage.CSHARP,
			"c#" to SyntaxLanguage.CSHARP,
			"dart" to SyntaxLanguage.DART,
			"go" to SyntaxLanguage.GO,
			"golang" to SyntaxLanguage.GO,
			"java" to SyntaxLanguage.JAVA,
			"javascript" to SyntaxLanguage.JAVASCRIPT,
			"js" to SyntaxLanguage.JAVASCRIPT,
			"jsx" to SyntaxLanguage.JAVASCRIPT,
			"kotlin" to SyntaxLanguage.KOTLIN,
			"kt" to SyntaxLanguage.KOTLIN,
			"kts" to SyntaxLanguage.KOTLIN,
			"perl" to SyntaxLanguage.PERL,
			"pl" to SyntaxLanguage.PERL,
			"php" to SyntaxLanguage.PHP,
			"python" to SyntaxLanguage.PYTHON,
			"py" to SyntaxLanguage.PYTHON,
			"py3" to SyntaxLanguage.PYTHON,
			"ruby" to SyntaxLanguage.RUBY,
			"rb" to SyntaxLanguage.RUBY,
			"rust" to SyntaxLanguage.RUST,
			"rs" to SyntaxLanguage.RUST,
			"swift" to SyntaxLanguage.SWIFT,
			"typescript" to SyntaxLanguage.TYPESCRIPT,
			"ts" to SyntaxLanguage.TYPESCRIPT,
			"tsx" to SyntaxLanguage.TYPESCRIPT,
		)
	}
}
