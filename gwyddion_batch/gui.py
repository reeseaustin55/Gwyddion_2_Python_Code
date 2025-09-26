"""Tkinter-based GUI for configuring and running batch jobs."""

from __future__ import absolute_import, print_function

import logging
import os
import threading

try:  # Python 2.7
    import Tkinter as tk
    import tkFileDialog
    import tkMessageBox
except ImportError:  # pragma: no cover - Python 3 fallback
    import tkinter as tk
    from tkinter import filedialog as tkFileDialog
    from tkinter import messagebox as tkMessageBox

try:  # Python 2.7
    import Queue as queue
except ImportError:  # pragma: no cover - Python 3 fallback
    import queue

from .config import BatchConfig, VideoSettings, StabilizationSettings
from .gwyddion_loader import import_gwyddion
from .processor import GwyddionBatchProcessor


DEFAULT_DATA_FOLDER = 'D\\AFM Images'


class _QueueHandler(logging.Handler):
    """Forward logging messages to a ``Queue`` for UI display."""

    def __init__(self, message_queue):
        logging.Handler.__init__(self)
        self._queue = message_queue

    def emit(self, record):  # pragma: no cover - UI side effect
        try:
            message = self.format(record)
        except Exception:  # pragma: no cover - defensive
            message = record.getMessage()
        self._queue.put(message)


class BatchProcessorGUI(object):  # pragma: no cover - UI heavy
    """Simple GUI to collect batch processor settings and run a job."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title('Gwyddion Batch Processor')
        self.log_queue = queue.Queue()
        self.processing_thread = None

        self._build_variables()
        self._build_ui()

    # ------------------------------------------------------------------ UI --
    def _build_variables(self):
        self.folder_var = tk.StringVar(value=DEFAULT_DATA_FOLDER)
        self.output_dir_var = tk.StringVar(
            value=self._default_output_for(DEFAULT_DATA_FOLDER)
        )
        self.channels_var = tk.StringVar(value='0')
        self.pixel_count_var = tk.StringVar(value='1024')
        self.filter_var = tk.StringVar()
        self.gwy_paths_var = tk.StringVar()

        self.video_enabled_var = tk.IntVar()
        self.video_output_var = tk.StringVar()
        self.ffmpeg_path_var = tk.StringVar(value='ffmpeg')
        self.frame_duration_var = tk.StringVar(value='0.1')
        self.frame_rate_var = tk.StringVar()
        self.pixel_format_var = tk.StringVar(value='yuv420p')
        self.extra_args_var = tk.StringVar()

        self.stabilize_var = tk.IntVar()
        self.shakiness_var = tk.StringVar(value='5')
        self.accuracy_var = tk.StringVar(value='9')
        self.stepsize_var = tk.StringVar(value='6')
        self.mincontrast_var = tk.StringVar(value='0.3')
        self.smoothing_var = tk.StringVar(value='15')
        self.tripod_var = tk.IntVar(value=1)
        self.crop_var = tk.IntVar(value=1)

    def _build_ui(self):
        main = tk.Frame(self.root)
        main.pack(fill='both', expand=True, padx=10, pady=10)
        main.columnconfigure(1, weight=1)

        row = 0
        self._add_labeled_entry(main, 'Folder:', self.folder_var, row,
                                browse_command=self._browse_folder)
        row += 1
        self._add_labeled_entry(main, 'Output directory:', self.output_dir_var, row,
                                browse_command=self._browse_output)
        row += 1
        self._add_labeled_entry(main, 'Channels (comma separated):', self.channels_var, row)
        row += 1
        self._add_labeled_entry(main, 'Pixel count:', self.pixel_count_var, row)
        row += 1
        self._add_labeled_entry(main, 'File filter (e.g. .spm):', self.filter_var, row)
        row += 1
        self._add_labeled_entry(main, 'Additional Gwyddion paths:', self.gwy_paths_var, row)
        row += 1

        self._video_entries = []
        self._stabilization_entries = []
        self._stabilization_checkbuttons = []

        video_frame = tk.LabelFrame(main, text='Video rendering')
        video_frame.grid(row=row, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
        video_frame.columnconfigure(1, weight=1)

        video_cb = tk.Checkbutton(video_frame, text='Enable video stitching',
                                  variable=self.video_enabled_var,
                                  command=self._toggle_video_fields)
        video_cb.grid(row=0, column=0, columnspan=3, sticky='w')

        self._add_labeled_entry(video_frame, 'Video output path:', self.video_output_var, 1,
                                entry_list=self._video_entries)
        self._add_labeled_entry(video_frame, 'ffmpeg path:', self.ffmpeg_path_var, 2,
                                entry_list=self._video_entries)
        self._add_labeled_entry(video_frame, 'Frame duration (s):', self.frame_duration_var, 3,
                                entry_list=self._video_entries)
        self._add_labeled_entry(video_frame, 'Frame rate (fps):', self.frame_rate_var, 4,
                                entry_list=self._video_entries)
        self._add_labeled_entry(video_frame, 'Pixel format:', self.pixel_format_var, 5,
                                entry_list=self._video_entries)
        self._add_labeled_entry(video_frame, 'Extra ffmpeg args:', self.extra_args_var, 6,
                                entry_list=self._video_entries)

        row += 1
        stab_frame = tk.LabelFrame(main, text='Video stabilization')
        stab_frame.grid(row=row, column=0, columnspan=3, sticky='nsew', pady=(10, 0))
        stab_frame.columnconfigure(1, weight=1)

        self._stabilize_checkbox = tk.Checkbutton(
            stab_frame,
            text='Enable stabilization',
            variable=self.stabilize_var,
            command=self._toggle_stabilization_fields,
        )
        self._stabilize_checkbox.grid(row=0, column=0, columnspan=3, sticky='w')

        self._add_labeled_entry(stab_frame, 'Shakiness:', self.shakiness_var, 1,
                                entry_list=self._stabilization_entries)
        self._add_labeled_entry(stab_frame, 'Accuracy:', self.accuracy_var, 2,
                                entry_list=self._stabilization_entries)
        self._add_labeled_entry(stab_frame, 'Step size:', self.stepsize_var, 3,
                                entry_list=self._stabilization_entries)
        self._add_labeled_entry(stab_frame, 'Min contrast:', self.mincontrast_var, 4,
                                entry_list=self._stabilization_entries)
        self._add_labeled_entry(stab_frame, 'Smoothing:', self.smoothing_var, 5,
                                entry_list=self._stabilization_entries)

        tripod_cb = tk.Checkbutton(stab_frame, text='Tripod mode', variable=self.tripod_var)
        tripod_cb.grid(row=6, column=0, columnspan=3, sticky='w')
        crop_cb = tk.Checkbutton(stab_frame, text='Crop to shared area', variable=self.crop_var)
        crop_cb.grid(row=7, column=0, columnspan=3, sticky='w')
        self._stabilization_checkbuttons.extend([tripod_cb, crop_cb])

        row += 1
        button_frame = tk.Frame(main)
        button_frame.grid(row=row, column=0, columnspan=3, pady=10, sticky='e')
        self.process_button = tk.Button(button_frame, text='Run Processing', command=self.start_processing)
        self.process_button.pack(side='right')

        row += 1
        log_frame = tk.LabelFrame(main, text='Status')
        log_frame.grid(row=row, column=0, columnspan=3, sticky='nsew')
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.log_text = tk.Text(log_frame, height=12, width=80, state='disabled')
        self.log_text.grid(row=0, column=0, sticky='nsew')

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview)
        scrollbar.grid(row=0, column=1, sticky='ns')
        self.log_text.configure(yscrollcommand=scrollbar.set)

        self._toggle_video_fields()
        self._toggle_stabilization_fields()

    def _add_labeled_entry(self, master, label_text, variable, row,
                           browse_command=None, entry_list=None):
        label = tk.Label(master, text=label_text)
        label.grid(row=row, column=0, sticky='e', padx=(0, 5), pady=2)
        entry = tk.Entry(master, textvariable=variable)
        entry.grid(row=row, column=1, sticky='we', pady=2)
        if browse_command is not None:
            button = tk.Button(master, text='Browse...', command=browse_command)
            button.grid(row=row, column=2, padx=(5, 0), pady=2)
        if entry_list is not None:
            entry_list.append(entry)
        return entry

    def _default_output_for(self, folder):
        if not folder:
            return ''
        path = os.path.join(folder, 'processed')
        if ('\\' in folder) and ('/' not in folder):
            path = path.replace('/', '\\')
        return path

    def _browse_folder(self):
        initial = self.folder_var.get() or DEFAULT_DATA_FOLDER
        path = tkFileDialog.askdirectory(initialdir=initial)
        if path:
            current_output = self.output_dir_var.get()
            previous_default = self._default_output_for(self.folder_var.get())
            self.folder_var.set(path)
            if (not current_output) or (current_output == previous_default):
                self.output_dir_var.set(self._default_output_for(path))

    def _browse_output(self):
        initial = (self.output_dir_var.get()
                   or self.folder_var.get()
                   or DEFAULT_DATA_FOLDER)
        path = tkFileDialog.askdirectory(initialdir=initial)
        if path:
            self.output_dir_var.set(path)

    def _toggle_video_fields(self):
        state = tk.NORMAL if self.video_enabled_var.get() else tk.DISABLED
        for entry in self._video_entries:
            entry.configure(state=state)
        if self.video_enabled_var.get():
            self._stabilize_checkbox.configure(state=tk.NORMAL)
        else:
            self.stabilize_var.set(0)
            self._stabilize_checkbox.configure(state=tk.DISABLED)
        self._toggle_stabilization_fields()

    def _toggle_stabilization_fields(self):
        video_enabled = bool(self.video_enabled_var.get())
        state = tk.NORMAL if (video_enabled and self.stabilize_var.get()) else tk.DISABLED
        for entry in self._stabilization_entries:
            entry.configure(state=state)
        for checkbox in self._stabilization_checkbuttons:
            checkbox.configure(state=state)
        if not video_enabled:
            self.stabilize_var.set(0)

    # --------------------------------------------------------------- runtime --
    def start_processing(self):
        if self.processing_thread and self.processing_thread.is_alive():
            return
        try:
            config = self._build_config()
        except ValueError as exc:
            tkMessageBox.showerror('Invalid settings', str(exc))
            return

        self._append_log('Starting batch processing...')
        self.process_button.configure(state=tk.DISABLED)
        self.processing_thread = threading.Thread(target=self._run_processing, args=(config,))
        self.processing_thread.daemon = True
        self.processing_thread.start()
        self.root.after(100, self._poll_log_queue)

    def _build_config(self):
        folder = self.folder_var.get().strip()
        if not folder:
            raise ValueError('Folder path is required')

        channels_text = self.channels_var.get().strip()
        channels = []
        if channels_text:
            for part in channels_text.replace(';', ',').split(','):
                part = part.strip()
                if not part:
                    continue
                channels.append(int(part))
        if not channels:
            raise ValueError('Please specify at least one channel number')

        pixel_count = int(self.pixel_count_var.get())
        file_filter = self.filter_var.get().strip() or None

        output_directory = self.output_dir_var.get().strip()
        if not output_directory:
            output_directory = os.path.join(folder, 'processed')

        gwy_paths_raw = self.gwy_paths_var.get().strip()
        gwy_paths = []
        if gwy_paths_raw:
            for part in gwy_paths_raw.replace(';', '\n').splitlines():
                part = part.strip()
                if part:
                    gwy_paths.append(part)

        video_enabled = bool(self.video_enabled_var.get())

        frame_duration = self._parse_float(self.frame_duration_var.get())
        frame_rate = self._parse_float(self.frame_rate_var.get())
        pixel_format = self.pixel_format_var.get().strip() or 'yuv420p'
        ffmpeg_path = self.ffmpeg_path_var.get().strip() or 'ffmpeg'
        video_output = self.video_output_var.get().strip() or None
        extra_args_text = self.extra_args_var.get().strip()
        extra_args = extra_args_text.split() if extra_args_text else []

        stabilization_enabled = video_enabled and bool(self.stabilize_var.get())
        stabilization = StabilizationSettings(
            enabled=stabilization_enabled,
            shakiness=int(self.shakiness_var.get() or 5),
            accuracy=int(self.accuracy_var.get() or 9),
            stepsize=int(self.stepsize_var.get() or 6),
            mincontrast=float(self.mincontrast_var.get() or 0.3),
            smoothing=int(self.smoothing_var.get() or 15),
            tripod=bool(self.tripod_var.get()),
            crop_shared_area=bool(self.crop_var.get()),
        )

        video_settings = VideoSettings(
            enabled=video_enabled,
            output_path=video_output,
            ffmpeg_path=ffmpeg_path,
            frame_rate=frame_rate,
            frame_duration=frame_duration,
            pixel_format=pixel_format,
            extra_args=extra_args,
            stabilization=stabilization,
        )

        return BatchConfig(
            folder_path=folder,
            channel_numbers=channels,
            pixel_count=pixel_count,
            file_filter=file_filter,
            gwyddion_paths=gwy_paths,
            output_directory=output_directory,
            video_settings=video_settings,
        )

    def _parse_float(self, value):
        value = value.strip() if value else ''
        if not value:
            return None
        return float(value)

    def _run_processing(self, config):
        handler = _QueueHandler(self.log_queue)
        formatter = logging.Formatter('%(levelname)s: %(message)s')
        handler.setFormatter(formatter)

        logger = logging.getLogger('GwyddionBatchGUI')
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

        try:
            gwy = import_gwyddion(config.gwyddion_paths, logger=logger)
            processor = GwyddionBatchProcessor(gwy, logger=logger)
            result = processor.process_folder(config)
            self.log_queue.put('Processing finished: %d/%d successes' %
                               (result.get('processed', 0), result.get('total', 0)))
            if result.get('output_directory'):
                self.log_queue.put('Processed images saved to %s' % result['output_directory'])
            for channel, details in sorted(result.get('per_channel', {}).items()):
                self.log_queue.put('Channel %d: %d/%d images saved' % (
                    channel,
                    details.get('processed', 0),
                    details.get('total', 0)))
            for channel, video_path in sorted(result.get('video_paths', {}).items()):
                self.log_queue.put('Channel %d video written to %s' % (channel, video_path))
        except Exception as exc:  # pragma: no cover - user feedback
            error_message = 'ERROR: %s' % exc
            self.log_queue.put(error_message)
            self.log_queue.put(('__ERROR__', str(exc)))
        finally:
            logger.removeHandler(handler)
            self.log_queue.put('__COMPLETE__')

    def _poll_log_queue(self):
        try:
            while True:
                message = self.log_queue.get_nowait()
                if message == '__COMPLETE__':
                    self.processing_thread = None
                    self.process_button.configure(state=tk.NORMAL)
                elif isinstance(message, tuple):
                    tag, payload = message
                    if tag == '__ERROR__':
                        tkMessageBox.showerror('Processing failed', payload)
                else:
                    self._append_log(message)
        except queue.Empty:
            pass
        if self.processing_thread is not None:
            self.root.after(100, self._poll_log_queue)

    def _append_log(self, message):
        self.log_text.configure(state='normal')
        self.log_text.insert('end', message + '\n')
        self.log_text.see('end')
        self.log_text.configure(state='disabled')

    def run(self):
        self.root.mainloop()

