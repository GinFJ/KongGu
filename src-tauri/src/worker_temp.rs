//! Pick the child process's TEMP *before* PyInstaller tries to unpack the worker.
use std::ffi::{c_void, OsStr};
use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::os::windows::ffi::OsStrExt;
use std::os::windows::fs::{MetadataExt, OpenOptionsExt};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::path::{Component, Path, PathBuf, Prefix};
use std::ptr::null_mut;
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{LocalFree, ERROR_ALREADY_EXISTS};
use windows_sys::Win32::Security::Authorization::{
    ConvertSecurityDescriptorToStringSecurityDescriptorW, ConvertSidToStringSidW,
    ConvertStringSecurityDescriptorToSecurityDescriptorW, GetSecurityInfo, SDDL_REVISION_1,
    SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{
    GetTokenInformation, TokenUser, DACL_SECURITY_INFORMATION, OWNER_SECURITY_INFORMATION,
    SECURITY_ATTRIBUTES, TOKEN_QUERY, TOKEN_USER,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateDirectoryW, GetDiskFreeSpaceExW, GetDriveTypeW, GetLogicalDrives, GetVolumePathNameW,
    FILE_ATTRIBUTE_REPARSE_POINT, FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_DELETE_ON_CLOSE,
    FILE_FLAG_OPEN_REPARSE_POINT, FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE,
    READ_CONTROL,
};
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
use windows_sys::Win32::System::WindowsProgramming::DRIVE_FIXED;

// The packaged worker currently expands to ~646 MiB. Reserve room for startup overhead.
const MIN_FREE_BYTES: u64 = 1024 * 1024 * 1024;
const SECURITY_INFO: u32 = OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION;
// Keep the selected directory open without delete sharing, including during extraction.
static DIRECTORY: Mutex<Option<File>> = Mutex::new(None);

fn wide(s: &OsStr) -> Vec<u16> {
    s.encode_wide().chain(Some(0)).collect()
}

fn win_result(ok: i32) -> io::Result<()> {
    if ok == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

// The Windows SID and SDDL conversion APIs allocate their output with LocalAlloc.
struct LocalMem(*mut c_void);
impl Drop for LocalMem {
    fn drop(&mut self) {
        unsafe { LocalFree(self.0); }
    }
}

impl LocalMem {
    fn string(&self) -> String {
        let start = self.0.cast::<u16>();
        let mut len = 0;
        unsafe {
            while *start.add(len) != 0 {
                len += 1;
            }
            String::from_utf16_lossy(std::slice::from_raw_parts(start, len))
        }
    }
}

fn user_sid() -> io::Result<String> {
    unsafe {
        let mut raw = null_mut();
        win_result(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut raw))?;
        let token = OwnedHandle::from_raw_handle(raw);
        let mut size = 0;
        GetTokenInformation(token.as_raw_handle(), TokenUser, null_mut(), 0, &mut size);
        if size == 0 {
            return Err(io::Error::last_os_error());
        }
        // TOKEN_USER contains a pointer and requires natural alignment.
        let mut buffer = vec![0usize; (size as usize).div_ceil(size_of::<usize>())];
        win_result(GetTokenInformation(
            token.as_raw_handle(), TokenUser, buffer.as_mut_ptr().cast(), size, &mut size,
        ))?;
        let user = &*buffer.as_ptr().cast::<TOKEN_USER>();
        let mut sid = null_mut();
        win_result(ConvertSidToStringSidW(user.User.Sid, &mut sid))?;
        Ok(LocalMem(sid.cast()).string())
    }
}

fn sddl(descriptor: &LocalMem) -> io::Result<String> {
    unsafe {
        let mut string = null_mut();
        win_result(ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor.0, SDDL_REVISION_1, SECURITY_INFO, &mut string, null_mut(),
        ))?;
        Ok(LocalMem(string.cast()).string())
    }
}

fn private_directory(parent: &Path, sid: &str) -> io::Result<(PathBuf, File)> {
    let path = parent.join(format!("Konggu-runtime-{sid}"));
    // Only the current user may populate the worker's DLL extraction directory.
    // Set the ACL at creation, never repair or adopt a preexisting shared directory.
    let security = format!("O:{sid}D:P(A;OICI;FA;;;{sid})");
    let mut descriptor = null_mut();
    unsafe {
        win_result(ConvertStringSecurityDescriptorToSecurityDescriptorW(
            wide(security.as_ref()).as_ptr(), SDDL_REVISION_1, &mut descriptor, null_mut(),
        ))?;
    }
    let expected = LocalMem(descriptor);
    let attrs = SECURITY_ATTRIBUTES {
        nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: expected.0,
        bInheritHandle: 0,
    };
    if unsafe { CreateDirectoryW(wide(path.as_os_str()).as_ptr(), &attrs) } == 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(ERROR_ALREADY_EXISTS as i32) {
            return Err(error);
        }
    }
    let directory = OpenOptions::new()
        .access_mode(READ_CONTROL | FILE_READ_ATTRIBUTES)
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT)
        .open(&path)?;
    let metadata = directory.metadata()?;
    if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(io::ErrorKind::PermissionDenied.into());
    }
    unsafe {
        let mut actual = null_mut();
        let code = GetSecurityInfo(
            directory.as_raw_handle(), SE_FILE_OBJECT, SECURITY_INFO,
            null_mut(), null_mut(), null_mut(), null_mut(), &mut actual,
        );
        if code != 0 {
            return Err(io::Error::from_raw_os_error(code as i32));
        }
        if sddl(&LocalMem(actual))? != sddl(&expected)? {
            return Err(io::ErrorKind::PermissionDenied.into());
        }
    }
    // Exercise an actual write. CREATE_NEW and delete-on-close cannot clobber an
    // earlier probe or any remaining extraction files.
    let probe = path.join(format!(".write-check-{}", std::process::id()));
    OpenOptions::new()
        .create_new(true)
        .write(true)
        .custom_flags(FILE_FLAG_DELETE_ON_CLOSE)
        .open(probe)?
        .write_all(b".")?;
    Ok((path, directory))
}

fn available_space(parent: &Path) -> Option<u64> {
    if !parent.is_absolute() {
        return None;
    }
    // Never probe UNC paths or query the capacity of mapped network drives.
    let Component::Prefix(prefix) = parent.components().next()? else {
        return None;
    };
    let (Prefix::Disk(letter) | Prefix::VerbatimDisk(letter)) = prefix.kind() else {
        return None;
    };
    let root = [letter as u16, b':' as u16, b'\\' as u16, 0];
    if unsafe { GetDriveTypeW(root.as_ptr()) } != DRIVE_FIXED {
        return None;
    }
    let dir = wide(parent.as_os_str());
    let mut volume = [0u16; 32768];
    let mut free = 0;
    unsafe {
        if GetVolumePathNameW(dir.as_ptr(), volume.as_mut_ptr(), volume.len() as u32) == 0
            || GetDriveTypeW(volume.as_ptr()) != DRIVE_FIXED
            || GetDiskFreeSpaceExW(dir.as_ptr(), &mut free, null_mut(), null_mut()) == 0
        {
            return None;
        }
    }
    Some(free)
}

fn select<T>(
    parents: impl IntoIterator<Item = PathBuf>,
    free: impl Fn(&Path) -> Option<u64>,
    mut prepare: impl FnMut(&Path) -> io::Result<T>,
) -> Result<T, String> {
    let mut highest = None;
    let mut unwritable = false;
    for parent in parents {
        let Some(bytes) = free(&parent) else {
            continue;
        };
        highest = Some(highest.unwrap_or(0u64).max(bytes));
        if bytes < MIN_FREE_BYTES {
            continue;
        }
        match prepare(&parent) {
            Ok(selected) => return Ok(selected),
            Err(_) => unwritable = true,
        }
    }
    Err(if unwritable {
        "本地识别功能启动失败：有磁盘空间充足，但无法创建可安全写入的解压目录。请将当前用户的 TEMP 和 TMP 指向有写入权限的本地固定磁盘目录，再重新打开空谷。".into()
    } else if let Some(max) = highest {
        format!("本地识别功能启动失败：解压至少需要 1 GiB 可用空间，已检查的本地磁盘最多剩余 {} MiB。请腾出空间后重试。", max / 1024 / 1024)
    } else {
        "本地识别功能启动失败：无法读取本地临时目录或固定磁盘的可用空间。请检查当前用户的 TEMP 和 TMP 目录权限后重试。".into()
    })
}

pub fn prepare() -> Result<String, String> {
    let sid = user_sid().map_err(|_| "本地识别功能启动失败：无法验证当前 Windows 用户的临时目录权限。")?;
    let drives = unsafe { GetLogicalDrives() };
    let roots = (0..26).filter(|i| drives & (1 << i) != 0)
        .map(|i| PathBuf::from(format!("{}:\\", (b'A' + i) as char)));
    let mut active = DIRECTORY.lock().map_err(|_| "本地解压目录正在恢复，请重新启动空谷。")?;
    let candidates = std::iter::once(std::env::temp_dir()).chain(roots);
    let (path, file) = select(candidates, available_space, |dir| private_directory(dir, &sid))?;
    let path = path.to_str().ok_or("本地解压路径包含无法传递给识别程序的字符，请调整 TEMP 和 TMP 后重试。")?;
    *active = Some(file);
    Ok(path.to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn skips_low_space_and_unwritable_locations_before_spawning() {
        assert_eq!(available_space(Path::new(r"\\server\share\temp")), None);
        let parents = ["preferred", "small", "readonly", "large"].map(PathBuf::from);
        let mut tried = Vec::new();
        let selected = select(parents, |p| match p.to_str().unwrap() {
            "preferred" => None,
            "small" => Some(MIN_FREE_BYTES - 1),
            _ => Some(MIN_FREE_BYTES),
        }, |p| {
            tried.push(p.to_owned());
            if p == Path::new("readonly") { return Err(io::ErrorKind::PermissionDenied.into()); }
            Ok(p.to_owned())
        }).unwrap();
        assert_eq!(selected, Path::new("large"));
        assert_eq!(tried, ["readonly", "large"].map(PathBuf::from));
    }

    #[test]
    fn prefers_configured_temp_and_distinguishes_space_and_permissions() {
        let parents = ["preferred", "other"].map(PathBuf::from);
        assert_eq!(select(parents.clone(), |_| Some(MIN_FREE_BYTES),
            |p| Ok(p.to_owned())).unwrap(), Path::new("preferred"));
        let full = select(parents.clone(), |_| Some(391 * 1024 * 1024), |_| Ok(()));
        assert!(full.unwrap_err().contains("391 MiB"));
        let denied = select(parents, |_| Some(MIN_FREE_BYTES), |_| {
            Err::<(), _>(io::ErrorKind::PermissionDenied.into())
        });
        assert!(denied.unwrap_err().contains("无法创建"));
    }

    #[test]
    fn private_directory_is_writable_reusable_and_rejects_an_untrusted_existing_directory() {
        let base = std::env::temp_dir().join(format!("konggu-temp-test-{}", std::process::id()));
        std::fs::create_dir(&base).unwrap();
        let sid = user_sid().unwrap();
        assert!(available_space(&base).is_some());
        let (path, first) = private_directory(&base, &sid).unwrap();
        let (same, second) = private_directory(&base, &sid).unwrap();
        assert_eq!(path, same);
        assert!(std::fs::read_dir(&path).unwrap().next().is_none());
        drop((first, second));
        std::fs::remove_dir(&path).unwrap();
        std::fs::create_dir(&path).unwrap();
        assert!(private_directory(&base, &sid).is_err());
        std::fs::remove_dir(path).unwrap();
        std::fs::remove_dir(base).unwrap();
    }
}
