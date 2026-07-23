using System.Drawing;
using System.Globalization;
using System.Windows.Forms;

namespace Switchboard.Watchtower;

internal sealed class WakeTimeDialog : Form
{
	readonly CheckBox _enableCheckBox;
	readonly DateTimePicker _timePicker;
	readonly Button _saveButton;
	readonly Button _clearButton;
	readonly Button _cancelButton;

	public bool WakeTimeEnabled { get; private set; }
	public TimeOnly WakeTime { get; private set; }

	public WakeTimeDialog(bool enabled, TimeOnly initialTime)
	{
		WakeTimeEnabled = enabled;
		WakeTime = initialTime;

		Text = "Daily Wake Time";
		ClientSize = new Size(330, 150);
		FormBorderStyle = FormBorderStyle.FixedDialog;
		MaximizeBox = false;
		MinimizeBox = false;
		ShowInTaskbar = false;
		StartPosition = FormStartPosition.CenterScreen;

		_enableCheckBox = new CheckBox
		{
			Text = "Enable daily wake time",
			Location = new Point(20, 20),
			AutoSize = true,
			Checked = enabled
		};
		_enableCheckBox.CheckedChanged += (_, _) => UpdatePickerState();

		var label = new Label
		{
			Text = "Wake time:",
			Location = new Point(20, 54),
			AutoSize = true
		};

		_timePicker = new DateTimePicker
		{
			Format = DateTimePickerFormat.Custom,
			CustomFormat = "h:mm tt",
			ShowUpDown = true,
			Location = new Point(100, 50),
			Size = new Size(120, 23),
			Value = DateTime.Today.Add(initialTime.ToTimeSpan())
		};

		_saveButton = new Button
		{
			Text = "Save",
			Location = new Point(20, 100),
			Size = new Size(80, 28),
			DialogResult = DialogResult.OK
		};
		_saveButton.Click += OnSaveClicked;

		_clearButton = new Button
		{
			Text = "Clear (Turn off)",
			Location = new Point(108, 100),
			Size = new Size(110, 28),
			DialogResult = DialogResult.OK
		};
		_clearButton.Click += OnClearClicked;

		_cancelButton = new Button
		{
			Text = "Cancel",
			Location = new Point(226, 100),
			Size = new Size(80, 28),
			DialogResult = DialogResult.Cancel
		};

		Controls.Add(_enableCheckBox);
		Controls.Add(label);
		Controls.Add(_timePicker);
		Controls.Add(_saveButton);
		Controls.Add(_clearButton);
		Controls.Add(_cancelButton);

		AcceptButton = _saveButton;
		CancelButton = _cancelButton;

		UpdatePickerState();
	}

	void UpdatePickerState()
	{
		_timePicker.Enabled = _enableCheckBox.Checked;
	}

	void OnSaveClicked(object? sender, EventArgs e)
	{
		WakeTimeEnabled = _enableCheckBox.Checked;
		WakeTime = TimeOnly.FromDateTime(_timePicker.Value);
	}

	void OnClearClicked(object? sender, EventArgs e)
	{
		WakeTimeEnabled = false;
	}
}
