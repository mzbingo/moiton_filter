"""Qt desktop review workspace; inference and file operations remain in the shared controller."""
import argparse
import json
import os
import queue
import sys
import threading
from pathlib import Path
from PySide6.QtCore import Qt,QTimer,QSize,QRectF
from PySide6.QtGui import QColor,QFont,QIcon,QImage,QPainter,QPen,QPixmap
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QFrame,QLabel,QPushButton,
    QVBoxLayout,QHBoxLayout,QGridLayout,QStackedWidget,QLineEdit,QComboBox,QCheckBox,
    QProgressBar,QTableWidget,QTableWidgetItem,QHeaderView,QAbstractItemView,QSplitter,
    QSlider,QFileDialog,QMessageBox,QPlainTextEdit,QScrollArea,QDialog,QListWidget,QSizePolicy,
    QStyledItemDelegate,QStyleOptionViewItem)
from app_common import app_root,save_json,video_files
from desktop_controller import DesktopController
from video_preview import VideoPreview,WIDTH,HEIGHT

LABELS={'match':'自动保留','manual_kept':'手动保留','review':'待确认','no_match':'未匹配',
        'no_person':'无人','error':'处理异常','manual_excluded':'已排除'}
COLORS={'match':('#e7f5ee','#21815c'),'manual_kept':('#e7f5ee','#21815c'),
        'review':('#fff3dc','#9f6a17'),'error':('#fdecec','#ba4141'),
        'manual_excluded':('#f0f1f5','#798294')}
STYLE='''
QWidget {font-family:"Microsoft YaHei UI"; font-size:13px; color:#252b3b;}
QMainWindow, QWidget#workspace {background:#f7f8fa;}
QFrame#sidebar {background:#f0f2f5; border-right:1px solid #e4e7ec;}
QFrame#card {background:white; border:1px solid #e9ecf1; border-radius:14px;}
QFrame#softCard {background:#f8f9fc; border:0; border-radius:10px;}
QLabel {background:transparent; border:0;}
QLabel#title {font-size:25px; font-weight:700; color:#1d2535;}
QLabel#section {font-size:16px; font-weight:600;}
QLabel#muted {color:#8790a1; font-size:12px;}
QLabel#metric {font-size:27px; font-weight:700; color:#263451;}
QLabel#brand {font-size:17px; font-weight:700; color:#23324d;}
QPushButton {background:white; border:1px solid #e1e5ec; border-radius:8px; padding:9px 15px; font-weight:500;}
QPushButton:hover {background:#f1f5fd; border-color:#cad7ed;}
QPushButton:pressed {background:#e4edff;}
QPushButton:disabled {color:#b4bcc9; background:#f2f4f7; border-color:#edf0f4;}
QPushButton#primary {background:#3370ff; color:white; border:1px solid #3370ff; font-weight:600;}
QPushButton#primary:hover {background:#2462ef;}
QPushButton#primary:disabled {background:#aec5f9; border-color:#aec5f9; color:#f7faff;}
QPushButton#danger {background:#fff; color:#c05050; border-color:#efd9d9;}
QPushButton#danger:hover {background:#fff0f0;}
QPushButton#nav {text-align:left; background:transparent; border:0; padding:12px 14px; color:#646e80;}
QPushButton#nav:hover {background:#e5e9f0;}
QPushButton#nav:checked {background:#e1eaff; color:#2863df; font-weight:600;}
QPushButton#tab {background:transparent; border:0; padding:8px 12px; color:#7d8798;}
QPushButton#tab:checked {background:#eaf0ff; color:#2c64df; font-weight:600;}
QLineEdit,QComboBox {background:#f8f9fc; border:1px solid #e5e9f0; border-radius:8px; padding:10px 12px; selection-background-color:#dce8ff;}
QLineEdit:focus,QComboBox:focus {border-color:#81a8ff; background:white;}
QComboBox::drop-down {border:0; width:28px;}
QComboBox QAbstractItemView {background:white; border:1px solid #e3e8f1; selection-background-color:#eaf0ff; color:#253047;}
QCheckBox {spacing:9px; color:#5e6a7f;}
QCheckBox::indicator {width:17px; height:17px; border:1px solid #ccd3df; border-radius:5px; background:white;}
QCheckBox::indicator:checked {background:#3370ff; border-color:#3370ff; image:none;}
QProgressBar {background:#edf1f8; border:0; border-radius:3px; max-height:6px; min-height:6px;}
QProgressBar::chunk {background:#3370ff; border-radius:3px;}
QTableWidget {background:white; border:0; gridline-color:transparent; selection-background-color:#eef4ff; selection-color:#26344f; outline:0;}
QTableWidget::item {border-bottom:1px solid #f1f3f7; padding:7px;}
QTableWidget::item:selected {background:#edf3ff;}
QHeaderView::section {background:#fafbfd; color:#8b94a4; border:0; border-bottom:1px solid #edf0f5; padding:10px 8px; font-size:12px;}
QScrollBar:vertical {background:transparent; width:7px; margin:2px;}
QScrollBar::handle:vertical {background:#d6dce6; border-radius:3px; min-height:30px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical {background:none;}
QSplitter::handle {background:transparent; width:14px;}
QSlider::groove:horizontal {height:4px; background:#e5eaf3; border-radius:2px;}
QSlider::sub-page:horizontal {background:#3370ff; border-radius:2px;}
QSlider::handle:horizontal {width:12px; margin:-4px 0; border-radius:6px; background:#3370ff;}
QPlainTextEdit {background:#f7f9fc; border:1px solid #e5e9f1; border-radius:9px; color:#526179; padding:12px; font-family:Consolas;}
QScrollArea {border:0; background:transparent;}
'''


def icon(kind,color='#778399'):
    pixmap=QPixmap(24,24);pixmap.fill(Qt.GlobalColor.transparent)
    painter=QPainter(pixmap);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen=QPen(QColor(color),1.7);pen.setCapStyle(Qt.PenCapStyle.RoundCap);painter.setPen(pen)
    if kind=='review':
        painter.drawRoundedRect(3,4,18,16,4,4);painter.drawLine(7,9,17,9);painter.drawLine(7,14,13,14)
    elif kind=='settings':
        for y,x in [(6,8),(12,16),(18,10)]:
            painter.drawLine(3,y,21,y);painter.setBrush(QColor('#f0f2f5'));painter.drawEllipse(x-2,y-2,4,4)
    elif kind=='history':
        painter.drawEllipse(4,4,16,16);painter.drawLine(12,7,12,12);painter.drawLine(12,12,16,14)
    elif kind=='folder':
        painter.drawRoundedRect(3,7,18,13,2,2);painter.drawLine(4,4,10,4);painter.drawLine(10,4,13,7)
    elif kind=='play':
        from PySide6.QtGui import QPolygon
        from PySide6.QtCore import QPoint
        painter.setBrush(QColor(color));painter.drawPolygon(QPolygon([QPoint(8,5),QPoint(19,12),QPoint(8,19)]))
    else:
        painter.drawRoundedRect(3,3,18,18,5,5);painter.drawLine(7,8,17,8);painter.drawLine(7,12,17,12);painter.drawLine(7,16,13,16)
    painter.end();return QIcon(pixmap)


def label(text='',name=None,wrap=False):
    widget=QLabel(text)
    if name:widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text,callback,name=None,symbol=None):
    widget=QPushButton(text)
    if name:widget.setObjectName(name)
    if symbol:widget.setIcon(icon(symbol));widget.setIconSize(QSize(19,19))
    widget.clicked.connect(callback)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    return widget


def card():
    widget=QFrame();widget.setObjectName('card');layout=QVBoxLayout(widget)
    layout.setContentsMargins(20,18,20,18);layout.setSpacing(12)
    return widget,layout


class PreviewLabel(QLabel):
    def __init__(self):
        super().__init__('选择视频，查看人物截图或播放完整画面')
        self.original=None;self.setAlignment(Qt.AlignmentFlag.AlignCenter);self.setMinimumSize(200,180)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
        self.setStyleSheet('background:#151e2e;color:#8997b0;border-radius:10px;padding:8px;')
        self.setWordWrap(True)

    def show_image(self,pixmap):
        self.original=pixmap
        if pixmap is None:self.clear();self.setText('选择视频，查看人物截图或播放完整画面')
        else:self.fit()

    def fit(self):
        if self.original and not self.original.isNull():
            self.setPixmap(self.original.scaled(self.size()-QSize(16,16),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def resizeEvent(self,event):
        super().resizeEvent(event);self.fit()


class StatusDelegate(QStyledItemDelegate):
    """Paint status badges without creating a QWidget for every result row."""
    def paint(self,painter,option,index):
        background_option=QStyleOptionViewItem(option)
        self.initStyleOption(background_option,index)
        background_option.text=''
        super().paint(painter,background_option,index)
        status=index.data(Qt.ItemDataRole.UserRole)
        text=LABELS.get(status,status or '')
        background,foreground=COLORS.get(status,('#f1f3f7','#7c8799'))
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        font=painter.font();font.setPixelSize(11);painter.setFont(font)
        width=painter.fontMetrics().horizontalAdvance(text)+14
        rect=QRectF(option.rect.x()+7,option.rect.center().y()-13,width,26)
        painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(background));painter.drawRoundedRect(rect,5,5)
        painter.setPen(QColor(foreground));painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text);painter.restore()


class ModernWindow(QMainWindow):
    def __init__(self,root=None):
        super().__init__()
        self.root=Path(root or app_root());self.controller=DesktopController(self.root);self.player=VideoPreview()
        self.playing=False;self.closing=False;self.preview_source=None;self.selected_key=None;self.backup=None
        self.filter_name='已保留';self.visible_rows=[];self.controls=[]
        self.thumbnail_cache={}
        self.review_busy=False;self.review_events=queue.Queue()
        self.render_timer=QTimer(self);self.render_timer.setSingleShot(True)
        self.render_timer.timeout.connect(self.render_rows)
        self.search_timer=QTimer(self);self.search_timer.setSingleShot(True)
        self.search_timer.timeout.connect(self.render_rows)
        try:self.settings=json.loads((self.root/'runtime/gui_settings.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):self.settings={}
        self.setWindowTitle('人物视频筛选 · 复核工作台');self.resize(1440,920);self.setMinimumSize(1060,700)
        self.setStyleSheet(STYLE)
        self.build()
        try:
            if self.controller.latest():self.loaded()
        except Exception as exc:self.status.setText('历史结果读取失败：'+str(exc))
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(66)

    def build(self):
        central=QWidget();central.setObjectName('workspace');self.setCentralWidget(central)
        shell=QHBoxLayout(central);shell.setContentsMargins(0,0,0,0);shell.setSpacing(0)
        sidebar=QFrame();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(202)
        side=QVBoxLayout(sidebar);side.setContentsMargins(16,26,16,20);side.setSpacing(7)
        brand=QHBoxLayout();mark=label('M');mark.setFixedSize(34,34);mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setStyleSheet('background:#3370ff;color:white;border-radius:10px;font-size:22px;font-weight:700;')
        brand.addWidget(mark);brand.addWidget(label('人物视频筛选','brand'));side.addLayout(brand)
        side.addSpacing(27);side.addWidget(label('工作空间','muted'));side.addSpacing(5)
        self.nav=[]
        for index,(text,symbol) in enumerate([('视频复核','review'),('任务设置','settings'),('历史与文件','history'),('运行日志','log')]):
            nav=button(text,lambda checked=False,i=index:self.navigate(i),'nav',symbol);nav.setCheckable(True)
            side.addWidget(nav);self.nav.append(nav)
        side.addStretch()
        local=QFrame();local.setObjectName('softCard');localbox=QVBoxLayout(local);localbox.setContentsMargins(12,12,12,12)
        localbox.addWidget(label('●  本机处理',wrap=False));localbox.addWidget(label('视频不上传云端','muted'))
        side.addWidget(local);side.addSpacing(8);side.addWidget(label('同一人物 · 同一套衣服','muted'))
        shell.addWidget(sidebar)
        body=QWidget();bodybox=QVBoxLayout(body);bodybox.setContentsMargins(28,24,28,20);bodybox.setSpacing(18)
        header=QHBoxLayout();titles=QVBoxLayout();titles.setSpacing(6)
        self.page_title=label('视频复核','title');self.subtitle=label('找到目标人物，让每一段视频都有明确的去向。','muted')
        titles.addWidget(self.page_title);titles.addWidget(self.subtitle);header.addLayout(titles);header.addStretch()
        self.stop_button=button('停止任务',self.stop);self.stop_button.setEnabled(False);header.addWidget(self.stop_button)
        self.start_button=button('开始筛选',self.start,'primary','play');header.addWidget(self.start_button)
        bodybox.addLayout(header)
        self.stack=QStackedWidget();bodybox.addWidget(self.stack,1)
        self.build_review();self.build_settings();self.build_history();self.build_logs()
        self.stack.setCurrentIndex(0);self.nav[0].setChecked(True)
        footer=QHBoxLayout();self.status=label('已准备好 · 可直接开始筛选','muted');footer.addWidget(self.status,1)
        self.device=label('本地 GPU 加速','muted');footer.addWidget(self.device);bodybox.addLayout(footer)
        shell.addWidget(body,1)

    def build_review(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(16)
        stats=QHBoxLayout();stats.setSpacing(12);self.metrics={}
        for name,caption,color in [('total','已检查视频','#263451'),('kept','已保留','#258665'),('review','待确认','#ae781b'),('excluded','手动排除','#7e8798')]:
            tile,box=card();box.setSpacing(5);box.addWidget(label(caption,'muted'));number=label('0','metric')
            number.setStyleSheet(f'color:{color};');box.addWidget(number);self.metrics[name]=number;stats.addWidget(tile)
        layout.addLayout(stats)
        self.progress=QProgressBar();self.progress.setRange(0,100);self.progress.setValue(0);self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        splitter=QSplitter(Qt.Orientation.Horizontal);splitter.setChildrenCollapsible(False)
        left,leftbox=card();leftbox.setContentsMargins(14,16,14,14)
        titlebar=QHBoxLayout();titlebar.addWidget(label('视频列表','section'));titlebar.addStretch()
        self.selection_count=label('0 项选中','muted');titlebar.addWidget(self.selection_count);leftbox.addLayout(titlebar)
        tabs=QHBoxLayout();tabs.setSpacing(2);self.filters={}
        for name in ['已保留','待确认','全部','未匹配','已排除','异常']:
            tab=button(name,lambda checked=False,n=name:self.set_filter(n),'tab');tab.setCheckable(True)
            tabs.addWidget(tab);self.filters[name]=tab
        tabs.addStretch();leftbox.addLayout(tabs)
        toolbar=QHBoxLayout();self.search=QLineEdit();self.search.setPlaceholderText('搜索视频文件名…')
        self.search.textChanged.connect(lambda _:self.search_timer.start(160));toolbar.addWidget(self.search,1)
        leftbox.addLayout(toolbar)
        actions=QHBoxLayout();self.keep_button=button('确认保留选中',lambda:self.review('keep'),'primary')
        self.exclude_button=button('确认排除选中',lambda:self.review('exclude'),'danger')
        actions.addWidget(self.keep_button);actions.addWidget(self.exclude_button);actions.addStretch();leftbox.addLayout(actions)
        self.review_hint=label('先选择视频，支持 Ctrl / Shift 多选','muted');leftbox.addWidget(self.review_hint)
        self.table=QTableWidget(0,3);self.table.setHorizontalHeaderLabels(['视频文件','结果','出现位置'])
        self.table.verticalHeader().setVisible(False);self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setIconSize(QSize(35,44))
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.table.setShowGrid(False)
        self.table.setItemDelegateForColumn(1,StatusDelegate(self.table))
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(1,108)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed);self.table.setColumnWidth(2,85)
        self.table.itemSelectionChanged.connect(self.selected);self.table.cellDoubleClicked.connect(lambda *_:self.toggle_play())
        leftbox.addWidget(self.table,1)
        self.empty=label('暂无视频结果。点击“开始筛选”，或打开历史结果。','muted',True);leftbox.addWidget(self.empty)
        right,rightbox=card();right.setMinimumWidth(315);rightbox.setContentsMargins(18,18,18,18)
        top=QHBoxLayout();top.addWidget(label('视频预览','section'));top.addStretch();top.addWidget(button('参考人物',self.show_references))
        rightbox.addLayout(top)
        self.preview_title=label('选择一段视频开始复核',wrap=True);rightbox.addWidget(self.preview_title)
        self.preview=PreviewLabel();rightbox.addWidget(self.preview,1)
        playback=QHBoxLayout();self.play_button=button('播放完整视频',self.toggle_play,symbol='play');playback.addWidget(self.play_button)
        playback.addWidget(button('外部打开',self.open_video));playback.addStretch();rightbox.addLayout(playback)
        self.slider=QSlider(Qt.Orientation.Horizontal);self.slider.setRange(0,60000);self.slider.sliderReleased.connect(self.seek_video)
        rightbox.addWidget(self.slider);self.time_label=label('00:00 / 00:00','muted');rightbox.addWidget(self.time_label)
        info=QFrame();info.setObjectName('softCard');infobox=QVBoxLayout(info);infobox.setContentsMargins(14,12,14,12)
        infobox.addWidget(label('匹配线索','muted'));self.detail=label('选择视频后，可查看衣着匹配依据。',wrap=True);infobox.addWidget(self.detail)
        rightbox.addWidget(info)
        rightbox.addWidget(label('排除后会保存副本，重跑同一目标时自动跳过。','muted',True))
        preview_scroll=QScrollArea();preview_scroll.setWidgetResizable(True);preview_scroll.setMinimumWidth(315)
        preview_scroll.setWidget(right)
        splitter.addWidget(left);splitter.addWidget(preview_scroll);splitter.setSizes([650,430]);layout.addWidget(splitter,1)
        self.stack.addWidget(page)

    def build_settings(self):
        page=QWidget();pagebox=QVBoxLayout(page);pagebox.setContentsMargins(0,0,0,0)
        scroll=QScrollArea();scroll.setWidgetResizable(True);content=QWidget();box=QVBoxLayout(content);box.setContentsMargins(0,0,8,0);box.setSpacing(16)
        folders,folderbox=card();folderbox.addWidget(label('视频与输出目录','section'))
        folderbox.addWidget(label('参考视频只放目标人物。命中与排除的视频均按原名、原样保存。','muted',True))
        self.paths={};self.counts={}
        for key,title,default in [('reference','参考视频',self.root/'input/reference_video'),('source','待筛选视频',self.root/'input/waiting_for_filter_videos'),
            ('output','保留输出',self.root/'output'),('excluded','手动排除',self.root/'manual_excluded')]:
            heading=QHBoxLayout();heading.addWidget(label(title));heading.addStretch();count=label('','muted');self.counts[key]=count;heading.addWidget(count)
            folderbox.addLayout(heading);row=QHBoxLayout();entry=QLineEdit(str(self.settings.get(key,default)))
            entry.editingFinished.connect(self.update_counts);self.paths[key]=entry;row.addWidget(entry,1)
            browse=button('选择目录',lambda checked=False,k=key:self.browse(k),symbol='folder');row.addWidget(browse)
            folderbox.addLayout(row);self.controls.extend([entry,browse])
        box.addWidget(folders)
        options,optionbox=card();optionbox.addWidget(label('筛选偏好','section'))
        row=QHBoxLayout();row.addWidget(label('筛选强度'));row.addStretch();self.mode=QComboBox()
        self.mode.addItems(['标准（推荐）','更严格','更宽松（多选）']);self.mode.setCurrentText(self.settings.get('mode','标准（推荐）'))
        self.mode.setMinimumWidth(200);row.addWidget(self.mode);optionbox.addLayout(row)
        self.archive=QCheckBox('归档旧输出，让本轮结果独立保存');self.archive.setChecked(self.settings.get('archive',True));optionbox.addWidget(self.archive)
        self.refresh=QCheckBox('重新分析视频（仍跳过手动排除项）');optionbox.addWidget(self.refresh)
        optionbox.addWidget(label('更宽松会增加待复核结果；上下装颜色仍须符合参考。','muted',True))
        self.controls.extend([self.mode,self.archive,self.refresh]);box.addWidget(options);box.addStretch()
        scroll.setWidget(content);pagebox.addWidget(scroll);self.stack.addWidget(page);self.update_counts()

    def build_history(self):
        page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(0,0,0,0);layout.setSpacing(16)
        history,box=card();box.addWidget(label('继续复核历史结果','section'))
        box.addWidget(label('打开已有报告，继续检查待确认视频和调整保留结果。','muted',True))
        self.report_label=label('尚未载入历史报告',wrap=True);box.addWidget(self.report_label)
        row=QHBoxLayout();row.addWidget(button('打开历史结果',self.choose_report,'primary','history'));row.addWidget(button('打开复核报告',self.open_report));row.addStretch();box.addLayout(row)
        layout.addWidget(history)
        destinations,box=card();box.addWidget(label('快速访问','section'))
        for title,subtitle,callback in [('保留的视频','原名原样复制的命中片段',lambda:self.open_folder(self.paths['output'].text())),
            ('手动排除的视频','已复核排除的副本，后续筛选自动跳过',lambda:self.open_folder(self.paths['excluded'].text())),
            ('归档与备份','旧输出和手动移出的视频副本',self.open_backup)]:
            row=QHBoxLayout();texts=QVBoxLayout();texts.addWidget(label(title));texts.addWidget(label(subtitle,'muted'));row.addLayout(texts,1)
            row.addWidget(button('打开目录',callback,symbol='folder'));box.addLayout(row)
        layout.addWidget(destinations);layout.addStretch();self.stack.addWidget(page)

    def build_logs(self):
        page,box=card();box.addWidget(label('运行日志','section'));box.addWidget(label('查看模型加载、任务进度与处理异常。','muted'))
        self.logs=QPlainTextEdit();self.logs.setReadOnly(True);self.logs.setMaximumBlockCount(1500);box.addWidget(self.logs,1);self.stack.addWidget(page)

    def navigate(self,index):
        self.stack.setCurrentIndex(index)
        for i,nav in enumerate(self.nav):nav.setChecked(i==index)
        titles=[('视频复核','查看匹配线索，确认保留或排除。'),('任务设置','准备参考视频，配置本轮筛选目录。'),
            ('历史与文件','继续复核历史任务，快速访问结果文件。'),('运行日志','查看本机任务进度和处理详情。')]
        self.page_title.setText(titles[index][0]);self.subtitle.setText(titles[index][1])

    def update_counts(self):
        for key,entry in self.paths.items():
            folder=Path(entry.text())
            try:self.counts[key].setText(f'{len(video_files(folder))} 个视频' if folder.is_dir() else '尚未创建' if key in ('output','excluded') else '目录不存在')
            except OSError:self.counts[key].setText('无法读取')

    def browse(self,key):
        path=QFileDialog.getExistingDirectory(self,'选择视频目录',self.paths[key].text())
        if path:self.paths[key].setText(path);self.update_counts()

    def save_settings(self):
        save_json(self.root/'runtime/gui_settings.json',{**{k:v.text() for k,v in self.paths.items()},'mode':self.mode.currentText(),'archive':self.archive.isChecked()})

    def start(self):
        if self.review_busy or self.controller.running:return
        try:
            threshold={'标准（推荐）':.72,'更严格':.78,'更宽松（多选）':.68}[self.mode.currentText()]
            self.save_settings()
            self.controller.start(*(self.paths[k].text() for k in ('reference','source','output')),threshold=threshold,
                archive=self.archive.isChecked(),refresh=self.refresh.isChecked(),excluded=self.paths['excluded'].text())
        except Exception as exc:self.error('无法开始筛选',exc);self.navigate(1);return
        self.clear_preview();self.thumbnail_cache.clear()
        self.set_running(True);self.set_filter('全部');self.navigate(0)
        self.progress.setRange(0,max(1,self.controller.summary['total']));self.progress.setValue(0)
        self.status.setText('正在准备模型与参考人物…')

    def stop(self):
        self.controller.stop();self.stop_button.setEnabled(False);self.status.setText('正在停止并保存已完成的结果…')

    def set_running(self,running):
        busy=running or self.review_busy
        self.start_button.setEnabled(not busy);self.stop_button.setEnabled(running)
        self.keep_button.setEnabled(not busy);self.exclude_button.setEnabled(not busy)
        self.review_hint.setText('正在复制视频并保存复核决定…' if self.review_busy else '筛选完成或停止后可手动确认' if running else '先选择视频，支持 Ctrl / Shift 多选')
        for control in self.controls:control.setEnabled(not busy)

    def set_filter(self,name):
        self.filter_name=name;self.render_rows()

    def render_rows(self,*_):
        if self.review_busy:return
        self.render_timer.stop();self.search_timer.stop()
        selected={r['file'] for r in self.selected_rows()}
        scroll=self.table.verticalScrollBar().value()
        rows=self.controller.rows;search=self.search.text().strip().casefold()
        counts={'已保留':sum(r['copied'] for r in rows),'待确认':sum(r['status']=='review' for r in rows),'全部':len(rows),
            '未匹配':sum(r['status'] in ('no_match','no_person') for r in rows),'已排除':sum(r['status']=='manual_excluded' for r in rows),'异常':sum(r['status']=='error' for r in rows)}
        for name,tab in self.filters.items():tab.setText(f'{name} {counts[name]}');tab.setChecked(name==self.filter_name)
        for key,value in [('total',len(rows)),('kept',counts['已保留']),('review',counts['待确认']),('excluded',counts['已排除'])]:self.metrics[key].setText(str(value))
        def include(row):
            return (self.filter_name=='全部' or self.filter_name=='已保留' and row['copied'] or self.filter_name=='待确认' and row['status']=='review' or
                self.filter_name=='未匹配' and row['status'] in ('no_match','no_person') or self.filter_name=='已排除' and row['status']=='manual_excluded' or self.filter_name=='异常' and row['status']=='error')
        self.visible_rows=[r for r in sorted(rows,key=lambda r:(not r['copied'],-r['score'])) if include(r) and (not search or search in r['file'].casefold())]
        self.table.blockSignals(True);self.table.setUpdatesEnabled(False)
        self.table.clearContents();self.table.setRowCount(len(self.visible_rows))
        from PySide6.QtCore import QItemSelectionModel
        for index,row in enumerate(self.visible_rows):
            item=QTableWidgetItem(row['file']);item.setToolTip(row['file']);self.table.setItem(index,0,item)
            if row.get('evidence') and self.controller.report:
                path=str(self.controller.report/row['evidence'])
                if path not in self.thumbnail_cache:
                    pixmap=QPixmap(path)
                    self.thumbnail_cache[path]=QIcon(pixmap.scaled(35,44,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
                item.setIcon(self.thumbnail_cache[path])
            status=QTableWidgetItem('');status.setToolTip(LABELS.get(row['status'],row['status']))
            status.setData(Qt.ItemDataRole.UserRole,row['status'])
            self.table.setItem(index,1,status);self.table.setItem(index,2,QTableWidgetItem(f'{row["time_seconds"]:.1f} 秒'))
            if row['file'] in selected:self.table.selectionModel().select(self.table.model().index(index,0),QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
        self.table.verticalScrollBar().setValue(scroll)
        self.table.setUpdatesEnabled(True);self.table.blockSignals(False);self.empty.setVisible(not self.visible_rows)
        self.empty.setText('这个分类还没有视频。试试其他分类，或开始新一轮筛选。')
        self.selection_count.setText(f'{len(self.selected_rows())} 项选中')
        if self.selected_rows():self.selected()
        elif self.selected_key is not None:self.clear_preview()

    def selected_rows(self):
        return [self.visible_rows[i.row()] for i in self.table.selectionModel().selectedRows() if i.row()<len(self.visible_rows)]

    def selected(self):
        rows=self.selected_rows();self.selection_count.setText(f'{len(rows)} 项选中')
        if not rows:return
        row=rows[0];key=(str(self.controller.report),row['file'])
        if key!=self.selected_key:
            evidence=self.controller.report/row['evidence'] if row.get('evidence') else None
            self.show_video(row['source_path'],evidence,max(0,row['time_seconds']-.6));self.selected_key=key
        self.detail.setText(f'{LABELS.get(row["status"],row["status"])}\n上装 {row["upper_color"]:.2f}   ·   下装 {row["lower_color"]:.2f}\n支持 {row["support_frames"]} 帧'+(f'\n{row["error"]}' if row.get('error') else ''))

    def clear_preview(self):
        self.player.stop();self.playing=False;self.selected_key=None;self.preview_source=None
        self.preview.show_image(None);self.preview_title.setText('尚未选择视频')
        self.play_button.setText('播放完整视频');self.slider.setRange(0,60000);self.slider.setValue(0)
        self.time_label.setText('00:00 / --:--');self.detail.setText('选择视频，查看匹配线索。')

    def show_video(self,path,evidence=None,position=0):
        self.player.stop();self.playing=False;self.selected_key=None;self.preview_source=Path(path)
        self.preview_title.setText(self.preview_source.name);self.play_button.setText('播放完整视频')
        self.slider.setRange(0,int(max(60,position+1)*1000));self.slider.setValue(int(position*1000))
        self.time_label.setText(f'{self.format_time(position)} / --:--')
        self.preview.show_image(QPixmap(str(evidence)) if evidence and Path(evidence).exists() else None)

    def toggle_play(self):
        if not self.preview_source:return
        if self.playing:
            self.slider.setValue(int(self.player.position*1000));self.player.stop();self.playing=False;self.play_button.setText('继续播放')
        else:self.seek_video()

    def seek_video(self):
        if not self.preview_source:return
        self.player.play(self.preview_source,self.slider.value()/1000);self.playing=True;self.play_button.setText('暂停播放')

    @staticmethod
    def format_time(value):
        value=max(0,int(value));return f'{value//60:02d}:{value%60:02d}'

    def review(self,decision):
        if self.review_busy or self.controller.running:return
        rows=self.selected_rows()
        if not rows:QMessageBox.information(self,'选择视频','先在视频列表中选择要确认的片段。支持 Ctrl / Shift 多选。');return
        try:
            self.save_settings()
            self.player.stop();self.playing=False;self.play_button.setText('播放完整视频')
            self.controller.excluded=Path(self.paths['excluded'].text()).resolve()
        except Exception as exc:self.error('操作未完成',exc);return
        self.review_busy=True;self.set_running(False)
        self.status.setText(f'正在{ "保留" if decision=="keep" else "排除" } {len(rows)} 个视频…')
        def work():
            try:self.review_events.put({'done':self.controller.review(rows,decision),'decision':decision})
            except Exception as exc:self.review_events.put({'error':str(exc)})
        threading.Thread(target=work,daemon=True).start()

    def loaded(self,update_paths=False):
        self.clear_preview();self.thumbnail_cache.clear()
        summary=self.controller.summary
        if update_paths:
            for key,field in [('reference','reference_dir'),('source','input_dir'),('output','output_dir'),('excluded','excluded_dir')]:
                if summary.get(field):self.paths[key].setText(summary[field])
        self.set_filter('已保留');self.report_label.setText(str(self.controller.report))
        self.progress.setRange(0,max(1,summary.get('total',len(self.controller.rows))));self.progress.setValue(len(self.controller.rows))
        self.status.setText(f'已载入 {len(self.controller.rows)} 个视频结果 · 可继续复核')
        if self.visible_rows:self.table.selectRow(0)
        self.update_counts()

    def choose_report(self):
        if self.review_busy:QMessageBox.information(self,'正在保存复核','请等待复核决定保存完成，再打开历史结果。');return
        if self.controller.running:QMessageBox.information(self,'任务进行中','请先停止任务，再打开历史结果。');return
        path,_=QFileDialog.getOpenFileName(self,'选择历史报告的 summary.json',str(self.root/'reports'),'筛选结果 (summary.json)')
        if path:
            try:self.controller.load(Path(path).parent);self.loaded(True);self.navigate(0)
            except Exception as exc:self.error('无法读取报告',exc)

    def open_path(self,path):
        try:
            if not Path(path).exists():raise ValueError('路径不存在：'+str(path))
            os.startfile(str(path))
        except Exception as exc:self.error('无法打开',exc)

    def open_folder(self,path):
        try:Path(path).mkdir(parents=True,exist_ok=True);self.open_path(path)
        except Exception as exc:self.error('无法打开目录',exc)

    def open_video(self):
        if self.preview_source:self.open_path(self.preview_source)

    def open_report(self):
        if self.controller.report:self.open_path(self.controller.report/'review.html')

    def open_backup(self):
        for path in [self.backup,self.controller.report/'removed_from_output' if self.controller.report else None,self.root/'reports/archives',self.root/'reports/previous_output_20261011']:
            if path and Path(path).exists():self.open_path(path);return
        QMessageBox.information(self,'暂无备份','归档旧输出或排除已保留视频后，将生成备份。')

    def reference_images(self,path):
        if not self.controller.report:return []
        references=self.controller.summary.get('references',[])
        for reference in references:
            source=reference.get('source_path')
            if source and Path(source).resolve()==Path(path).resolve():
                return [self.controller.report/name for name in reference.get('evidence',[])
                    if (self.controller.report/name).is_file()]
        # Modern reports identify each reference by its full source path. Never
        # borrow another video's thumbnails merely because the stems coincide.
        if any('source_path' in reference or 'evidence' in reference for reference in references):return []
        return sorted((self.controller.report/'evidence').glob(f'ref_{Path(path).stem}_*.jpg'))

    def show_references(self):
        # Task settings may already describe the next target. While reviewing a
        # report, its reference videos must stay paired with its result/evidence.
        current_reference=self.controller.summary.get('reference_dir') if self.controller.report else None
        folder=Path(current_reference or self.paths['reference'].text())
        paths=video_files(folder)
        if not paths:
            message=f'当前报告的参考目录中没有视频：\n{folder}' if current_reference else '请在任务设置中选择包含目标人物的参考目录。'
            QMessageBox.information(self,'没有参考视频',message);return
        dialog=QDialog(self);dialog.setWindowTitle('参考人物');dialog.resize(560,360);box=QVBoxLayout(dialog)
        box.addWidget(label('选择参考视频，检查本轮目标人物','section'));listing=QListWidget();listing.addItems([p.name for p in paths]);listing.setCurrentRow(0);box.addWidget(listing)
        def show():
            index=listing.currentRow()
            if index<0:return
            path=paths[index];images=self.reference_images(path)
            self.show_video(path,images[len(images)//2] if images else None);self.detail.setText('参考视频 · 本轮要寻找的目标人物');dialog.accept();self.navigate(0)
        box.addWidget(button('预览选中参考',show,'primary'));listing.itemDoubleClicked.connect(lambda _:show());dialog.exec()

    def error(self,title,exception):QMessageBox.warning(self,title,str(exception))

    def poll(self):
        dirty=False;finished=False
        for _ in range(100):
            try:event=self.controller.events.get_nowait()
            except queue.Empty:break
            kind=event['type']
            if kind=='log':self.logs.appendPlainText(event['message'])
            elif kind=='started':
                self.progress.setRange(0,max(1,event['total']))
                self.report_label.setText(str(event.get('report') or self.controller.report))
            elif kind=='phase' and not self.closing:self.status.setText(event['message'])
            elif kind=='device':self.device.setText(event['name']+' · 本机处理')
            elif kind=='backup':self.backup=Path(event['path']);self.logs.appendPlainText(f'旧输出已归档：{self.backup}')
            elif kind=='result':self.controller.rows.append(event['row']);self.progress.setValue(event['processed']);dirty=True
            elif kind in ('fatal','cancelled'):self.status.setText(event['message']);self.logs.appendPlainText(event['message'])
            elif kind=='finished':
                self.controller.summary=event;self.report_label.setText(str(self.controller.report))
                self.status.setText(('已停止' if event['cancelled'] else '筛选完成')+f' · 已检查 {event["processed"]} · 已保留 {event["copied"]} · 跳过已排除 {event.get("skipped_excluded",0)}'+(f' · 异常 {event["errors"]}' if event['errors'] else ''))
            elif kind=='exit':
                self.set_running(False)
                if self.controller.report and (self.controller.report/'results.csv').exists():
                    try:
                        self.controller.load(self.controller.report)
                        self.report_label.setText(str(self.controller.report))
                    except Exception as exc:self.logs.appendPlainText(str(exc))
                if self.controller.summary.get('status')=='failed':
                    self.status.setText('任务失败：'+str(self.controller.summary.get('fatal_error') or '请检查运行日志'))
                elif event['code']:self.status.setText('任务未完全完成，请检查异常结果和运行日志')
                dirty=True;finished=True;self.update_counts()
                if self.closing:self.close();return
        if finished:self.render_rows()
        elif dirty and not self.render_timer.isActive():self.render_timer.start(120)
        try:
            result=self.review_events.get_nowait()
        except queue.Empty:pass
        else:
            self.review_busy=False;self.set_running(self.controller.running)
            self.render_rows();self.update_counts()
            if result.get('error'):
                self.status.setText('复核未全部完成，已完成的决定已保存')
                if not self.closing:self.error('操作未完成',result['error'])
            else:self.status.setText(f'已{ "保留" if result["decision"]=="keep" else "排除" } {len(result["done"])} 个视频 · 复核决定已保存')
            if self.closing:self.close();return
        for _ in range(20):
            try:event=self.player.events.get_nowait()
            except queue.Empty:break
            if event['generation']!=self.player.generation:continue
            if event['type']=='duration':self.slider.setMaximum(max(100,int(event['duration']*1000)))
            elif event['type'] in ('ended','error'):
                self.playing=False;self.play_button.setText('重新播放')
                if event['type']=='ended':self.slider.setValue(0)
                else:self.detail.setText('预览失败：'+event['message'])
        try:
            generation,data,position=self.player.frames.get_nowait()
            if generation==self.player.generation:
                image=QImage(data,WIDTH,HEIGHT,WIDTH*3,QImage.Format.Format_RGB888).copy()
                self.preview.show_image(QPixmap.fromImage(image))
                if self.playing:
                    if not self.slider.isSliderDown():self.slider.setValue(int(position*1000))
                    self.time_label.setText(f'{self.format_time(position)} / {self.format_time(self.player.duration)}')
        except queue.Empty:pass

    def closeEvent(self,event):
        self.player.stop()
        if self.review_busy:
            self.closing=True;self.status.setText('正在保存复核决定，完成后自动关闭…');event.ignore()
        elif self.controller.running:
            self.closing=True;self.controller.stop();self.stop_button.setEnabled(False)
            self.status.setText('正在保存结果，任务停止后自动关闭…');event.ignore()
        else:
            try:self.save_settings()
            except OSError:pass
            self.timer.stop();self.render_timer.stop();self.search_timer.stop();event.accept()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-test',action='store_true');parser.add_argument('--screenshot',type=Path)
    args=parser.parse_args();application=QApplication(sys.argv);application.setStyle('Fusion')
    application.setFont(QFont('Microsoft YaHei UI',10));window=ModernWindow()
    screen=application.primaryScreen().availableGeometry();window.resize(min(1440,screen.width()-50),min(920,screen.height()-50));window.show()
    if args.smoke_test:
        def finish():
            ok=window.keep_button.isVisible() and window.exclude_button.isVisible() and window.table.height()>100
            if args.screenshot:window.grab().save(str(args.screenshot))
            save_json(window.root/'runtime/modern_gui_smoke.json',{'ok':ok,'rows':len(window.controller.rows),'visible_rows':window.table.rowCount(),
                'buttons_visible':window.keep_button.isVisible() and window.exclude_button.isVisible(),'size':[window.width(),window.height()]})
            window.close();application.exit(0 if ok else 1)
        QTimer.singleShot(1200,finish)
    return application.exec()


if __name__=='__main__':sys.exit(main())
