// Simulator-only helper for recording a local demo. Delivered as source.
#import <UIKit/UIKit.h>
#import <TargetConditionals.h>
#if !TARGET_OS_SIMULATOR
#error This driver is for iOS Simulator only.
#endif
static BOOL typing;
static __weak UIResponder *activeTarget;
@interface UIResponder (ShowtimeCaption)
- (void)showtimeCaptureTarget:(id)sender;
@end
@implementation UIResponder (ShowtimeCaption)
- (void)showtimeCaptureTarget:(id)sender { activeTarget = self; }
@end
static NSString *file(NSString *name) {return [NSTemporaryDirectory() stringByAppendingPathComponent:name];}
static void writeJSON(id value, NSString *name) {
    [[NSJSONSerialization dataWithJSONObject:value options:0 error:NULL] writeToFile:file(name) atomically:YES];
}
static void configure(id<UITextInputTraits> view) {
    view.autocapitalizationType=UITextAutocapitalizationTypeNone;
    view.autocorrectionType=UITextAutocorrectionTypeNo;
    view.spellCheckingType=UITextSpellCheckingTypeNo;
}
static void typeCaption(NSString *caption) {
    activeTarget=nil;
    [UIApplication.sharedApplication sendAction:@selector(showtimeCaptureTarget:) to:nil from:nil forEvent:nil];
    id view = activeTarget;
    if ((![view isKindOfClass:UITextView.class] && ![view isKindOfClass:UITextField.class]) || [[view text] length]) {
        writeJSON(@{@"error":@"Expected a focused empty caption text view"},@"showtime-caption-result.json"); return;
    }
    typing = YES;
    configure(view);
    // Scope appearance to this field and this input session, including aborts.
    UIColor *originalTint = [view tintColor];
    [view setTintColor:UIColor.clearColor];
    void (^finish)(NSTimer *, NSDictionary *) = ^(NSTimer *timer, NSDictionary *result) {
        [timer invalidate];
        [view setTintColor:originalTint];
        typing = NO;
        writeJSON(result, @"showtime-caption-result.json");
    };
    NSMutableArray *log=[NSMutableArray new];
    __block NSUInteger index=0;
    [NSTimer scheduledTimerWithTimeInterval:1.0/30 repeats:YES block:^(NSTimer *timer){
        @try {
            if (![view isFirstResponder]) { finish(timer, @{@"error":@"Caption field lost focus"}); return; }
            NSRange next = [caption rangeOfComposedCharacterSequenceAtIndex:index];
            [view insertText:[caption substringWithRange:next]];
            index = NSMaxRange(next);
            [log addObject:@{@"time":@([[NSDate date] timeIntervalSince1970]),@"visible_text":[view text]}];
            if (index == caption.length) {
                finish(timer, @{@"characters":log,@"value":[view text]});
            }
        } @catch (NSException *exception) {
            finish(timer, @{@"error":exception.reason ?: @"Caption input failed"});
        }
    }];
}
__attribute__((constructor)) static void installCaptionDriver(void) {
    dispatch_async(dispatch_get_main_queue(),^{
        [NSNotificationCenter.defaultCenter addObserverForName:UITextViewTextDidBeginEditingNotification object:nil queue:nil usingBlock:^(NSNotification *n){configure(n.object);[n.object reloadInputViews];}];
        writeJSON(@{@"ready":@YES},@"showtime-caption-ready.json");
        [NSTimer scheduledTimerWithTimeInterval:.025 repeats:YES block:^(NSTimer *timer){
            if (typing) return;
            NSString *path=file(@"showtime-caption-command.txt");if (![NSFileManager.defaultManager fileExistsAtPath:path]) return;
            NSString *caption=[NSString stringWithContentsOfFile:path encoding:NSUTF8StringEncoding error:NULL];
            [NSFileManager.defaultManager removeItemAtPath:path error:NULL];
            if (caption.length) typeCaption(caption);
            else writeJSON(@{@"error":@"Expected non-empty UTF-8 text"}, @"showtime-caption-result.json");
        }];
    });
}
