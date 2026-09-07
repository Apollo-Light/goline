@tool
extends EditorPlugin

## Goline AI editor plugin (Stage 2, external-CLI model).
##
## Adds a dock that drives the local `goline_cli.py` tool (explain / code /
## debug workflows) from inside the editor. This is a thin UI seam: all real
## work happens in the pure-Python CLI, so it works with any stock Godot
## binary — no engine rebuild required.

const GolineDockScene := preload("res://addons/goline_ai/goline_dock.tscn")

var _dock: Control


func _enter_tree() -> void:
	_dock = GolineDockScene.instantiate()
	add_control_to_dock(DOCK_SLOT_LEFT_BR, _dock)
	print("[goline_ai] dock added (editor plugin loaded)")


func _exit_tree() -> void:
	if _dock:
		remove_control_from_docks(_dock)
		_dock.queue_free()
		_dock = null
	print("[goline_ai] dock removed")