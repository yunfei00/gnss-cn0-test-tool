WORKSPACE_STYLE = """
QMainWindow, QWidget#workspace { background: #f2f5fa; }
QWidget { color: #172740; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
QWidget#rail { background: #13243b; }
QWidget#rail QLabel { color: #e5eef9; }
QLabel#brand { color: #ffffff; font-size: 19px; font-weight: bold; }
QWidget#rail QLabel#brandMark { background: #35e0b8; color: #13243b; border-radius: 12px;
                               font-size: 17px; font-weight: bold; }
QToolButton#navigate { color: #c0d2e9; border: 0; border-radius: 7px;
                        padding: 10px 15px; text-align: left; font-weight: bold; }
QToolButton#navigate:hover { background: #203957; }
QToolButton#navigate:checked { background: #254562; color: #ffffff; }
QLabel#pageTitle { font-size: 28px; font-weight: bold; color: #142743; }
QLabel#sectionTitle { font-size: 16px; font-weight: bold; }
QLabel#muted { color: #526781; }
QLabel#statusTitle { font-size: 15px; font-weight: bold; }
QLabel#pill { background: #e6f6f2; border-radius: 8px; color: #17634f;
               padding: 6px 11px; font-weight: bold; }
QLabel#fieldName { color: #526781; font-size: 11px; font-weight: bold; }
QLabel#chartEmpty { background: #f6f9fd; border: 1px solid #e2eaf3;
                    border-radius: 6px; color: #526781; }
QFrame#card { background: #ffffff; border: 1px solid #dce5ef; border-radius: 12px; }
QFrame#runBar { background: #eaf0f8; border: 1px solid #dce5ef; border-radius: 12px; }
QPushButton, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff; border: 1px solid #cfdae8; border-radius: 6px;
    min-height: 18px; padding: 7px 10px;
}
QComboBox { padding-right: 25px; }
QComboBox::drop-down { width: 22px; border: 0; }
QComboBox::down-arrow { image: none; }
QPushButton:hover, QComboBox:hover { background: #f1f6fc; border-color: #a6bad1; }
QPushButton:focus, QComboBox:focus, QLineEdit:focus { border-color: #276ae0; }
QPushButton:disabled { background: #eff3f8; border-color: #dfe7ef; color: #7b8ba0; }
QPushButton#primary { background: #2258c8; border-color: #2258c8; color: #ffffff;
                       font-weight: bold; padding: 9px 20px; }
QPushButton#primary:hover { background: #1d4eae; }
QPushButton#primary:disabled { background: #dce7fb; color: #527098; border-color: #dce7fb; }
QPushButton#quiet { background: transparent; border-color: transparent; color: #2158ac; }
QPushButton#quiet:hover, QPushButton#quiet:checked { background: #edf4ff; }
QCheckBox#sceneTile { background: #f5f8fc; border: 1px solid #dce5ef;
                      border-radius: 7px; padding: 6px 9px; spacing: 7px; }
QCheckBox#sceneTile:checked { background: #eaf2ff; border-color: #9cbcf0; color: #184893; }
QCheckBox#sceneTile:hover { border-color: #789bd8; }
QPlainTextEdit#runLog { background: #f6f9fd; border: 1px solid #e1e9f5;
                        border-radius: 6px; padding: 6px 9px; color: #35516e; }
QTableWidget { background: #ffffff; alternate-background-color: #f6f9fd; border: 1px solid #e2eaf3;
               border-radius: 6px; selection-background-color: #dceaff; selection-color: #132742; }
QTableWidget::item { padding: 5px 7px; }
QHeaderView::section { background: #edf3fa; color: #365371; border: 0;
                       border-bottom: 1px solid #dbe5f0; padding: 8px 7px; font-weight: bold; }
QHeaderView::section:vertical { padding: 5px; }
QTableCornerButton::section { background: #edf3fa; border: 0; }
QProgressBar { color: #172740; background: #dce7f8; border: 0; border-radius: 6px;
               min-height: 20px; text-align: center; }
QProgressBar::chunk { background: #83aff0; border-radius: 6px; }
"""
