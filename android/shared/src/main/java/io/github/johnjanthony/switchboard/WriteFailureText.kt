package io.github.johnjanthony.switchboard

/** Toast text for a rejected RTDB write. Pure so unit tests can pin it. */
fun writeFailureToastText(label: String, error: String?): String =
	"Write failed: $label" + (error?.let { " ($it)" } ?: "")
