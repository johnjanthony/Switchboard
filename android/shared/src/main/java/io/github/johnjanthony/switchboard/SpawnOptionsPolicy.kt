package io.github.johnjanthony.switchboard

import io.github.johnjanthony.switchboard.network.SpawnOptions

/**
 * Pure derivations for the spawn dialog's model/effort pickers. The catalog is
 * server-published (RTDB spawn_options node); when it is absent the pickers offer
 * only the CLI default, so spawn never blocks on the catalog.
 */
object SpawnOptionsPolicy {

	fun modelOptions(options: SpawnOptions?, agent: String): List<String> {
		val cli = if (agent == "antigravity") options?.antigravity else options?.claude
		return cli?.models?.mapNotNull { m -> m.id.takeIf { it.isNotBlank() } } ?: emptyList()
	}

	/**
	 * Effort tiers for the current selection. Antigravity never shows effort
	 * (it is baked into the model id). With no model picked, the union of all
	 * tiers in catalog order (the flag then applies to the CLI default model).
	 */
	fun effortOptions(options: SpawnOptions?, agent: String, modelId: String?): List<String> {
		if (agent == "antigravity") return emptyList()
		val models = options?.claude?.models ?: return emptyList()
		if (modelId == null) {
			return models.flatMap { it.efforts ?: emptyList() }.distinct()
		}
		return models.firstOrNull { it.id == modelId }?.efforts ?: emptyList()
	}

	fun showEffortPicker(agent: String): Boolean = agent != "antigravity"
}
