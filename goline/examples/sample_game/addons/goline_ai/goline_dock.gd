@tool
extends PanelContainer

## Goline AI dock: calls the local `goline_cli.py` with the current file /
## instruction / pasted error and shows the result. Runs the CLI on a
## background thread so the editor stays responsive.

const CLI_REL_PATH := "goline/cli/goline_cli.py"

var _thread: Thread
var _busy := false

@onready var _cli_path: LineEdit = %CLIPath
@onready var _provider: OptionButton = %Provider
@onready var _instruction: LineEdit = %Instruction
@onready var _error_text: TextEdit = %ErrorText
@onready var _output: RichTextLabel = %Output
@onready var _status: Label = %Status
@onready var _explain_btn: Button = %ExplainBtn
@onready var _edit_btn: Button = %EditBtn
@onready var _debug_btn: Button = %DebugBtn


func _ready() -> void:
	_provider.add_item("opencode", 0)
	_provider.add_item("claude", 1)
	_provider.select(0)
	var detected := _detect_cli_path()
	if detected != "":
		_cli_path.text = detected
	else:
		_cli_path.placeholder_text = "path to goline_cli.py (Auto)"
	_log("Set up. Choose a file in the editor (e.g. a .gd script), then\n"
		+ "Explain, Edit, or Debug.")


func _exit_tree() -> void:
	_join_thread()


# --- UI handlers -------------------------------------------------------------

func _on_auto_pressed() -> void:
	var detected := _detect_cli_path()
	if detected != "":
		_cli_path.text = detected
		_log("CLI path set to: " + detected)
	else:
		_log("Could not auto-detect goline_cli.py. Set the path manually "
			+ "(press Auto again after opening this project from the Goline "
			+ "repo, or type the full path to goline_cli.py).")


func _on_explain_pressed() -> void:
	var path := _current_file_path()
	if path == "":
		_log("No script selected. Open the script you want explained in the "
			+ "script editor, then press Explain again.")
		return
	_run_cli([
		"--explain", path,
		"--provider", _selected_provider(),
	])


func _on_edit_pressed() -> void:
	var path := _current_file_path()
	if path == "":
		_log("No script selected. Open the script you want to edit in the "
			+ "script editor, then press Edit again.")
		return
	var instruction := _instruction.text.strip_edges()
	if instruction == "":
		_log("Type an instruction first (e.g. 'add a _process that prints "
			+ "velocity').")
		return
	_run_cli([
		"--code", path,
		"--instruction", instruction,
		"--provider", _selected_provider(),
		"--guard",
	])


func _on_debug_pressed() -> void:
	var text := _error_text.text.strip_edges()
	if text == "":
		_log("Paste an error / backtrace in the box above, then press Debug.")
		return
	_run_cli([
		"--debug", text,
		"--provider", _selected_provider(),
		"--guard",
	])


func _on_copy_pressed() -> void:
	DisplayServer.clipboard_set(_output.text)
	_set_status("copied to clipboard")


# --- helpers -----------------------------------------------------------------

func _selected_provider() -> String:
	return _provider.get_item_text(_provider.selected)


func _current_file_path() -> String:
	var script_editor := EditorInterface.get_script_editor()
	if script_editor == null:
		return ""
	var script: Resource = script_editor.get_current_script()
	if script == null or script.resource_path == "":
		return ""
	return ProjectSettings.globalize_path(script.resource_path)


func _detect_cli_path() -> String:
	var root := ProjectSettings.globalize_path("res://")
	var dir := root.rstrip("/\\")
	while dir != "":
		var candidate := dir.path_join(CLI_REL_PATH)
		if FileAccess.file_exists(candidate):
			return candidate
		var parent := dir.get_base_dir()
		if parent == dir:
			break
		dir = parent
	return ""


func _python_path() -> String:
	var override := OS.get_environment("GOLINE_PYTHON")
	if override != "":
		return override
	return "python"


# --- background CLI execution ------------------------------------------------

func _run_cli(args: PackedStringArray) -> void:
	if _busy:
		_log("A command is still running — wait for it to finish.")
		return
	var cli := _cli_path.text.strip_edges()
	if cli == "":
		_log("CLI path is empty. Press Auto to detect it, or set it manually.")
		return
	_busy = true
	_set_buttons_enabled(false)
	_set_status("running…")
	var argv := PackedStringArray()
	argv.append(cli)
	argv.append_array(args)
	_thread = Thread.new()
	_thread.start(Callable(self, "_thread_run").bind(argv))


func _thread_run(argv: PackedStringArray) -> void:
	var cli := argv[0]
	var output: Array = []
	var code := OS.execute(_python_path(), argv, output, true, false)
	var text := ""
	for i in output.size():
		text += str(output[i]) + "\n"
	text = text.strip_edges()
	if code == -1:
		text = "Could not run the CLI.\nCLI: " + cli + \
			"\nPython: " + _python_path() + \
			"\nIs the path correct? (Press Auto to detect.)"
	call_deferred("_on_cli_done", code, text)


func _on_cli_done(code: int, text: String) -> void:
	if _thread != null and _thread.is_started():
		_thread.wait_to_finish()
	_thread = null
	_busy = false
	_set_buttons_enabled(true)
	_set_status("done (exit %d)" % code)
	if text != "":
		_log(text)
	else:
		_log("(no output)")


func _join_thread() -> void:
	if _thread != null and _thread.is_started():
		_thread.wait_to_finish()
	_thread = null
	_busy = false


func _set_buttons_enabled(enabled: bool) -> void:
	_explain_btn.disabled = not enabled
	_edit_btn.disabled = not enabled
	_debug_btn.disabled = not enabled


func _set_status(msg: String) -> void:
	_status.text = msg


func _log(text: String) -> void:
	_output.clear()
	_output.text = text