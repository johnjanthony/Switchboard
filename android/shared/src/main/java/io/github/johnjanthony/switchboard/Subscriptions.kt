package io.github.johnjanthony.switchboard

/**
 * Collects unsubscribe lambdas so a ViewModel can detach every Firebase listener it
 * attached, exactly once, in onCleared(). Not thread-safe by design: registration and
 * disposal happen on the main thread (ViewModel lifecycle + Firebase callbacks).
 * dispose() is idempotent; an add() after dispose() runs the unsub immediately so a
 * callback racing teardown cannot leak a listener.
 */
class Subscriptions {
	private val unsubs = mutableListOf<() -> Unit>()
	private var disposed = false

	fun add(unsub: () -> Unit) {
		if (disposed) { unsub(); return }
		unsubs.add(unsub)
	}

	fun dispose() {
		if (disposed) return
		disposed = true
		for (u in unsubs.asReversed()) u()
		unsubs.clear()
	}

	val size: Int get() = unsubs.size
}
