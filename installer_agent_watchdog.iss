// Pascal script included from the [Code] section of installer.iss.
//
// The application can start its bundled Agent Watchdog as a background process
// ("MarkdownViewer.exe --agent-watchdog"). It has no window
// and outlives the application on purpose, so it keeps MarkdownViewer.exe and
// the Python runtime in the installation folder locked: an upgrade or an
// uninstall would fail on files in use.
//
// Setup therefore stops it first, in two steps:
//
// 1. It runs the installed "MarkdownViewer.exe --agent-watchdog-stop" (hidden,
//    waited for). That command asks the bundled Watchdog to exit cleanly and
//    touches nothing else: a Watchdog the user runs with their own Python is
//    never asked to stop. It is run only when the installed copy ships the
//    bundled Watchdog (the same versions that know the option); an older
//    version would not understand the option, and has nothing to stop anyway.
// 2. As a fallback (the command failed, or Setup runs under another account
//    whose data folder is not the user's) it ends what is left. A process is
//    ended only when BOTH hold:
//   * its executable is the MarkdownViewer.exe of this installation folder, and
//   * the first argument after the executable is exactly --agent-watchdog
//     (a document whose path merely contains that text is somebody's window).
// A Watchdog the user runs with their own Python (python.exe / pythonw.exe), a
// copy of the application in another folder, and the application's own windows
// are never touched. The application starts a fresh copy the next time it runs.

const
  AgentWatchdogFlag = '--agent-watchdog';
  AgentWatchdogStopFlag = '--agent-watchdog-stop';
  AgentWatchdogScript = '_internal\agent_watchdog\aiwatch.py';
  AgentWatchdogWaitMs = 5000;

procedure AskBundledAgentWatchdogToStop(const ExePath: String; AsOriginalUser: Boolean);
var
  ResultCode: Integer;
  Started: Boolean;
begin
  try
    if not FileExists(ExePath) then
      Exit;
    { Without the bundled copy this installation never started one, and its
      executable may be too old to know the option. }
    if not FileExists(ExtractFilePath(ExePath) + AgentWatchdogScript) then
      Exit;
    if AsOriginalUser then
      Started := ExecAsOriginalUser(ExePath, AgentWatchdogStopFlag, '', SW_HIDE,
        ewWaitUntilTerminated, ResultCode)
    else
      Started := Exec(ExePath, AgentWatchdogStopFlag, '', SW_HIDE,
        ewWaitUntilTerminated, ResultCode);
    if Started then
      Log(Format('Bundled Agent Watchdog stop command exited with %d', [ResultCode]))
    else
      Log('Bundled Agent Watchdog stop command could not be run: ' + SysErrorMessage(ResultCode));
  except
    Log('Bundled Agent Watchdog stop command failed: ' + GetExceptionMessage);
  end;
end;

function IsBlank(C: Char): Boolean;
begin
  Result := (C = ' ') or (C = #9);
end;

{ The first argument of a Windows command line: the program name (quoted or
  not) is skipped, then one argument is read with its quotes removed. }
function FirstArgument(const CommandLine: String): String;
var
  I, N: Integer;
  InQuotes: Boolean;
begin
  Result := '';
  N := Length(CommandLine);
  I := 1;
  InQuotes := False;
  while (I <= N) and (InQuotes or not IsBlank(CommandLine[I])) do
  begin
    if CommandLine[I] = '"' then
      InQuotes := not InQuotes;
    I := I + 1;
  end;
  while (I <= N) and IsBlank(CommandLine[I]) do
    I := I + 1;
  InQuotes := False;
  while (I <= N) and (InQuotes or not IsBlank(CommandLine[I])) do
  begin
    if CommandLine[I] = '"' then
      InQuotes := not InQuotes
    else
      Result := Result + CommandLine[I];
    I := I + 1;
  end;
end;

function IsBundledAgentWatchdog(Process: Variant; const ExePath: String): Boolean;
var
  Path, CommandLine: String;
begin
  Result := False;
  { Both are Null for a process this account may not inspect: leave it alone. }
  if VarIsNull(Process.ExecutablePath) or VarIsNull(Process.CommandLine) then
    Exit;
  Path := Process.ExecutablePath;
  if CompareText(Path, ExePath) <> 0 then
    Exit;
  CommandLine := Process.CommandLine;
  Result := CompareText(FirstArgument(CommandLine), AgentWatchdogFlag) = 0;
end;

{ Ends the matching processes; returns how many still run afterwards. }
function EndBundledAgentWatchdogOnce(const ExePath: String; Terminate: Boolean): Integer;
var
  Locator, Services, Processes, Process: Variant;
  I: Integer;
begin
  Result := 0;
  Locator := CreateOleObject('WbemScripting.SWbemLocator');
  Services := Locator.ConnectServer('.', 'root\CIMV2');
  Processes := Services.ExecQuery(
    'SELECT ProcessId, ExecutablePath, CommandLine FROM Win32_Process ' +
    'WHERE Name = "' + ExtractFileName(ExePath) + '"');
  for I := 0 to Processes.Count - 1 do
  begin
    Process := Processes.ItemIndex(I);
    if IsBundledAgentWatchdog(Process, ExePath) then
    begin
      Result := Result + 1;
      if Terminate then
      begin
        Log(Format('Ending bundled Agent Watchdog, process %d', [Integer(Process.ProcessId)]));
        try
          Process.Terminate(0);
        except
          Log('Could not end it: ' + GetExceptionMessage);
        end;
      end;
    end;
  end;
end;

procedure StopBundledAgentWatchdog(const ExePath: String);
var
  Waited: Integer;
begin
  try
    if EndBundledAgentWatchdogOnce(ExePath, True) = 0 then
      Exit;
    { Windows releases the file locks a moment after the process is gone. }
    Waited := 0;
    while (Waited < AgentWatchdogWaitMs)
      and (EndBundledAgentWatchdogOnce(ExePath, False) > 0) do
    begin
      Sleep(250);
      Waited := Waited + 250;
    end;
  except
    { Never block Setup: at worst it reports the files in use, as before. }
    Log('Bundled Agent Watchdog check failed: ' + GetExceptionMessage);
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  { The user's own account: that is where the Watchdog keeps its data. }
  AskBundledAgentWatchdogToStop(ExpandConstant('{app}\{#MyAppExeName}'), True);
  StopBundledAgentWatchdog(ExpandConstant('{app}\{#MyAppExeName}'));
  Result := '';
end;

function InitializeUninstall(): Boolean;
begin
  AskBundledAgentWatchdogToStop(ExpandConstant('{app}\{#MyAppExeName}'), False);
  StopBundledAgentWatchdog(ExpandConstant('{app}\{#MyAppExeName}'));
  Result := True;
end;
